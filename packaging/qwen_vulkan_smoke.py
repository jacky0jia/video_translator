"""Experimental Vulkan Qwen smoke; isolated from the production installer."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_qwen_backends import audit_candidate, CANDIDATES, COMMON_FILES
from app.services.tts.qwen_voices import QwenVoiceCatalog
from app.workers.llama_tts_worker import LlamaTTSWorkerClient

DIAGNOSTIC_PROFILES = {
    "baseline": {"arguments": (), "environment": {}},
    "no-op-offload": {"arguments": ("--no-op-offload",), "environment": {}},
    "no-vulkan-optimizations": {"arguments": (), "environment": {
        "GGML_VK_DISABLE_FUSION": "1", "GGML_VK_DISABLE_GRAPH_OPTIMIZE": "1",
    }},
}


class CandidateProcessError(RuntimeError):
    def __init__(self, code, log_name):
        self.returncode = code
        super().__init__(f"Vulkan candidate exited with code {code}; see {log_name}")


def failure_diagnostic(error, log):
    """Classify only an observed failed process; a log token alone is not proof."""
    if isinstance(error, asyncio.CancelledError):
        return {"code": "cancelled"}
    if isinstance(error, TimeoutError):
        return {"code": "timeout"}
    if not isinstance(error, CandidateProcessError):
        return {"code": "validation_or_runtime_error"}
    result = {"code": "process_failed", "returncode": error.returncode}
    for line in log.splitlines():
        if ("ggml-vulkan.cpp:" in line and "GGML_ASSERT(dst->op != GGML_OP_GET_ROWS" in line
                and "a_offset == 0 && b_offset == 0 && d_offset == 0" in line
                and line.rstrip().endswith("failed")):
            result.update(code="vulkan_get_rows_alignment", evidence=line.strip()[:500])
            break
    return result


@contextmanager
def temporary_runtime(report):
    directory = tempfile.TemporaryDirectory(prefix="subtitle-translator-vulkan-smoke-")
    try:
        yield Path(directory.name)
    finally:
        original_error = sys.exc_info()[1]
        # Windows crash reporting / virus scanning may briefly retain DLL handles.
        for attempt in range(21):
            try:
                directory.cleanup()
                report["runtime_cleanup"] = "succeeded"
                break
            except PermissionError as exc:
                if attempt < 20:
                    time.sleep(0.25)
                    continue
                report["runtime_cleanup"] = "failed"
                report["cleanup_error"] = str(exc)
                report["remaining_runtime_directory"] = directory.name
                if original_error is None:
                    raise
                original_error.add_note(f"Runtime cleanup also failed: {exc}")


def extract_runtime(archive_path: Path, runtime: Path, inventory: dict) -> None:
    """Extract only the attested TTS dependencies, never server/RPC executables."""
    selected = [item for item in inventory["files"] if item["name"] in COMMON_FILES
                or item["name"] == "ggml-vulkan.dll"
                or re.fullmatch(r"ggml-cpu-[a-z0-9]+\.dll", item["name"])]
    with zipfile.ZipFile(archive_path) as archive:
        for item in selected:
            content = archive.read(item["name"])
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise RuntimeError("Candidate changed after audit")
            (runtime / item["name"]).write_bytes(content)


def select_device(log: str, requested: str | None) -> tuple[str, str]:
    devices = dict(re.findall(r"^\s*(Vulkan\d+):\s*(.+)$", log, re.MULTILINE))
    if requested is not None:
        if requested not in devices:
            raise RuntimeError("Requested Vulkan device was not enumerated")
        return requested, devices[requested]
    if len(devices) != 1:
        raise RuntimeError("Expected one Vulkan device; select an enumerated --device explicitly")
    return next(iter(devices.items()))


def placement_evidence(log: str, device: str, mmproj_device: str = "vulkan") -> dict:
    escaped = re.escape(device)
    backbone = re.search(rf"{escaped}\s+model buffer size\s*=\s*([\d.]+) MiB", log)
    expected_mmproj = device if mmproj_device == "vulkan" else "CPU"
    mmproj = re.findall(r"CLIP using (\S+) backend", log)
    layers = re.search(r"offloaded (\d+)/(\d+) layers to GPU", log)
    if not (backbone and float(backbone[1]) > 0 and mmproj and set(mmproj) == {expected_mmproj}
            and layers and int(layers[1]) == int(layers[2]) and int(layers[1]) > 0):
        raise RuntimeError("Log does not prove the requested backbone and mmproj placement")
    return {"backbone_buffer_mib": float(backbone[1]), "mmproj_device": expected_mmproj,
            "offloaded_layers": int(layers[1]), "total_layers": int(layers[2])}


async def run_process(arguments, runtime: Path, log: Path, timeout: float, *, profile="baseline"):
    diagnostic = DIAGNOSTIC_PROFILES[profile]
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(("LLAMA_", "GGML_")):
            del environment[key]
    windows = Path(environment.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join(map(str, (runtime, windows / "System32", windows)))
    environment.update(diagnostic["environment"])
    if "--list-devices" not in arguments:
        arguments = (*arguments, *diagnostic["arguments"])
    with log.open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            *map(str, arguments), cwd=runtime, env=environment,
            stdin=asyncio.subprocess.DEVNULL, stdout=output, stderr=output,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        try:
            code = await asyncio.wait_for(process.wait(), timeout)
            if code:
                raise CandidateProcessError(code, log.name)
        finally:
            if process.returncode is None:
                process.kill()
                await asyncio.wait_for(process.wait(), 10)


async def run_smoke(archive: Path, model_dir: Path, output_dir: Path, *, device=None, timeout=300, mmproj_device="vulkan", profile="baseline"):
    if os.name != "nt":
        raise RuntimeError("The pinned candidate requires Windows")
    if not 0 < timeout <= 600:
        raise ValueError("Timeout must be between 0 and 600 seconds")
    if mmproj_device not in {"vulkan", "cpu"}:
        raise ValueError("mmproj device must be vulkan or cpu")
    if profile not in DIAGNOSTIC_PROFILES:
        raise ValueError("Unknown diagnostic profile")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {"state": "failed", "production_ready": False, "amd_hardware_validated": False,
              "runtime_version": "b10792-c5a5535e6", "requested_mmproj_device": mmproj_device,
              "diagnostic_profile": profile, "diagnostic_settings": DIAGNOSTIC_PROFILES[profile]}
    started = time.monotonic()
    try:
        inventory = await asyncio.to_thread(audit_candidate, archive, "vulkan")
        report["archive_sha256"] = inventory["archive_sha256"]
        release = json.loads((ROOT / "packaging/llama-tts/package-manifest.b10792.json").read_text(encoding="utf-8"))
        model = release["model"]
        paths = []
        for file_key, hash_key in (("filename", "sha256"), ("mmproj_filename", "mmproj_sha256")):
            path = (model_dir / model[file_key]).resolve(strict=True)
            actual = await asyncio.to_thread(QwenVoiceCatalog._sha256, path)
            if actual != model[hash_key]:
                raise RuntimeError(f"Pinned model checksum mismatch: {file_key}")
            paths.append(path)
        report["model_sha256"] = model["sha256"]
        report["mmproj_sha256"] = model["mmproj_sha256"]
        catalog = await asyncio.to_thread(QwenVoiceCatalog, ROOT / "models/voices/librivox_public_domain")
        voice = catalog.resolve("zh_female_1", "zh")
        report["voice_sha256"] = voice.sha256
        text = "这是 Vulkan 配音验证。"
        with temporary_runtime(report) as runtime:
            extract_runtime(archive, runtime, inventory)
            executable = runtime / "llama-tts.exe"
            devices_log = output_dir / "devices.log"
            await run_process((executable, "--list-devices"), runtime, devices_log, min(timeout, 30), profile=profile)
            selected, description = select_device(devices_log.read_text(encoding="utf-8", errors="replace"), device)
            report.update(device=selected, device_description=description)
            log = output_dir / "synthesis.log"
            wav = runtime / "result.wav"
            await run_process((executable, "-m", paths[0], "-mm", paths[1],
                               "-dev", selected, "-ngl", "all", "-mmdev", selected if mmproj_device == "vulkan" else "none",
                               "--log-verbosity", "4",
                               "--tts-speaker-file", voice.reference_path, "--tts-lang", "zh",
                               "-p", text, "-n", "256", "-o", wav), runtime, log, timeout, profile=profile)
            audio = await asyncio.to_thread(LlamaTTSWorkerClient._read_valid_wav, wav, text)
            report["placement"] = placement_evidence(log.read_text(encoding="utf-8", errors="replace"), selected, mmproj_device)
            (output_dir / "result.wav").write_bytes(audio)
            report.update(state="succeeded", wav_bytes=len(audio), wav_sha256=hashlib.sha256(audio).hexdigest())
    except BaseException as exc:
        report["state"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        # Preserve placement even when synthesis aborts after model initialization.
        log_path = output_dir / ("synthesis.log" if (output_dir / "synthesis.log").exists() else "devices.log")
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        report["failure"] = failure_diagnostic(exc, log_text)
        if "device" in report:
            try:
                report["placement"] = placement_evidence(log_text, report["device"], mmproj_device)
            except RuntimeError:
                pass
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (output_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="A new directory for logs, report and WAV")
    parser.add_argument("--device", help="An enumerated Vulkan device, e.g. Vulkan0")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--diagnostic-profile", choices=tuple(DIAGNOSTIC_PROFILES), default="baseline",
                        help="Fixed experimental profiles; never changes the installed runtime")
    parser.add_argument("--mmproj-device", choices=("vulkan", "cpu"), default="vulkan",
                        help="CPU is an explicit diagnostic hybrid, not a full Vulkan pass")
    args = parser.parse_args()
    try:
        report = asyncio.run(run_smoke(args.archives_dir / CANDIDATES["vulkan"]["filename"],
                                      args.model_dir, args.output_dir, device=args.device, timeout=args.timeout,
                                      mmproj_device=args.mmproj_device, profile=args.diagnostic_profile))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Vulkan smoke failed: {exc}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
