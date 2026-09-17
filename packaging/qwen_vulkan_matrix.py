"""Run the reviewed Qwen Vulkan language and recovery matrix on Windows."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from qwen_vulkan_smoke import (  # noqa: E402
    CANDIDATES,
    LlamaTTSWorkerClient,
    QwenVoiceCatalog,
    audit_candidate,
    extract_runtime,
    placement_evidence,
    run_process,
    select_device,
    temporary_runtime,
)


LANGUAGE_MATRIX = (
    ("zh", "zh_female_1", "这是 AMD Vulkan 中文语音验证。"),
    ("en", "en_female_1", "This is the AMD Vulkan English voice test."),
    ("ja", "ja_female_1", "これは AMD Vulkan の日本語音声テストです。"),
    ("ko", "ko_female_1", "이것은 AMD Vulkan 한국어 음성 테스트입니다."),
)


def _arguments(executable, model, mmproj, device, voice, language, text, output):
    return (
        executable,
        "-m", model,
        "-mm", mmproj,
        "-dev", device,
        "-ngl", "all",
        "-mmdev", device,
        "--log-verbosity", "4",
        "--tts-speaker-file", voice.reference_path,
        "--tts-lang", language,
        "-p", text,
        "-n", "256",
        "-o", output,
    )


async def _synthesize(
    runtime: Path,
    output_dir: Path,
    *,
    name: str,
    model: Path,
    mmproj: Path,
    device: str,
    voice,
    language: str,
    text: str,
    timeout: float,
) -> dict:
    executable = runtime / "llama-tts.exe"
    wav = runtime / f"{name}.wav"
    log = output_dir / f"{name}.log"
    started = time.monotonic()
    await run_process(
        _arguments(executable, model, mmproj, device, voice, language, text, wav),
        runtime,
        log,
        timeout,
    )
    audio = await asyncio.to_thread(LlamaTTSWorkerClient._read_valid_wav, wav, text)
    evidence = placement_evidence(
        log.read_text(encoding="utf-8", errors="replace"), device
    )
    destination = output_dir / f"{name}.wav"
    destination.write_bytes(audio)
    return {
        "language": language,
        "voice": voice.id,
        "voice_sha256": voice.sha256,
        "wav_bytes": len(audio),
        "wav_sha256": hashlib.sha256(audio).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "placement": evidence,
    }


async def run_matrix(
    archive: Path,
    model_dir: Path,
    voice_dir: Path,
    release_manifest: Path,
    output_dir: Path,
    *,
    device: str | None = None,
    timeout: float = 300,
    cancellation_delay: float = 0.5,
) -> dict:
    if os.name != "nt":
        raise RuntimeError("The pinned candidate requires Windows")
    if not 0 < timeout <= 600:
        raise ValueError("Timeout must be between 0 and 600 seconds")
    if not 0.05 <= cancellation_delay <= 5:
        raise ValueError("Cancellation delay must be between 0.05 and 5 seconds")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "state": "failed",
        "production_ready": False,
        "amd_hardware_validated": False,
        "runtime_version": "b10792-c5a5535e6",
        "matrix": [],
        "repeat": None,
        "cancellation": {"attempted": False, "cancelled": False},
        "timeout_recovery": {"attempted": False, "timed_out": False, "recovered": False},
    }
    started = time.monotonic()
    try:
        inventory = await asyncio.to_thread(audit_candidate, archive, "vulkan")
        report["archive_sha256"] = inventory["archive_sha256"]
        release = json.loads(release_manifest.read_text(encoding="utf-8"))
        model_metadata = release["model"]
        model = (model_dir / model_metadata["filename"]).resolve(strict=True)
        mmproj = (model_dir / model_metadata["mmproj_filename"]).resolve(strict=True)
        for path, expected in (
            (model, model_metadata["sha256"]),
            (mmproj, model_metadata["mmproj_sha256"]),
        ):
            if await asyncio.to_thread(QwenVoiceCatalog._sha256, path) != expected:
                raise RuntimeError(f"Pinned model checksum mismatch: {path.name}")
        catalog = await asyncio.to_thread(QwenVoiceCatalog, voice_dir)
        with temporary_runtime(report) as runtime:
            extract_runtime(archive, runtime, inventory)
            executable = runtime / "llama-tts.exe"
            devices_log = output_dir / "devices.log"
            await run_process(
                (executable, "--list-devices"), runtime, devices_log, min(timeout, 30)
            )
            selected, description = select_device(
                devices_log.read_text(encoding="utf-8", errors="replace"), device
            )
            report.update(device=selected, device_description=description)

            for language, voice_id, text in LANGUAGE_MATRIX:
                voice = catalog.resolve(voice_id, language)
                report["matrix"].append(await _synthesize(
                    runtime,
                    output_dir,
                    name=f"matrix-{language}",
                    model=model,
                    mmproj=mmproj,
                    device=selected,
                    voice=voice,
                    language=language,
                    text=text,
                    timeout=timeout,
                ))

            repeat_language, repeat_voice_id, repeat_text = LANGUAGE_MATRIX[0]
            repeat_voice = catalog.resolve(repeat_voice_id, repeat_language)
            report["repeat"] = await _synthesize(
                runtime,
                output_dir,
                name="repeat-zh",
                model=model,
                mmproj=mmproj,
                device=selected,
                voice=repeat_voice,
                language=repeat_language,
                text=repeat_text,
                timeout=timeout,
            )

            long_text = "这是用于验证 Vulkan 取消和进程清理的较长语音。" * 20
            cancel_log = output_dir / "cancellation.log"
            cancel_output = runtime / "cancelled.wav"
            report["cancellation"]["attempted"] = True
            cancellation = asyncio.create_task(run_process(
                _arguments(
                    executable, model, mmproj, selected, repeat_voice,
                    repeat_language, long_text, cancel_output,
                ),
                runtime,
                cancel_log,
                timeout,
            ))
            await asyncio.sleep(cancellation_delay)
            cancellation.cancel()
            try:
                await cancellation
            except asyncio.CancelledError:
                report["cancellation"]["cancelled"] = True
            else:
                raise RuntimeError("Vulkan cancellation request completed before cancellation")

            timeout_log = output_dir / "forced-timeout.log"
            timeout_output = runtime / "timed-out.wav"
            report["timeout_recovery"]["attempted"] = True
            try:
                await run_process(
                    _arguments(
                        executable, model, mmproj, selected, repeat_voice,
                        repeat_language, long_text, timeout_output,
                    ),
                    runtime,
                    timeout_log,
                    0.05,
                )
            except TimeoutError:
                report["timeout_recovery"]["timed_out"] = True
            else:
                raise RuntimeError("Forced Vulkan timeout unexpectedly completed")

            report["timeout_recovery"]["recovery"] = await _synthesize(
                runtime,
                output_dir,
                name="recovery-zh",
                model=model,
                mmproj=mmproj,
                device=selected,
                voice=repeat_voice,
                language=repeat_language,
                text="Vulkan 超时后恢复成功。",
                timeout=timeout,
            )
            report["timeout_recovery"]["recovered"] = True
            if not all(item["placement"]["mmproj_device"] == selected for item in report["matrix"]):
                raise RuntimeError("Matrix did not keep every mmproj context on Vulkan")
            report["state"] = "succeeded"
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--voice-dir", type=Path, default=ROOT / "models/voices/librivox_public_domain")
    parser.add_argument("--release-manifest", type=Path, default=ROOT / "packaging/llama-tts/package-manifest.b10792.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--cancellation-delay", type=float, default=0.5)
    args = parser.parse_args()
    archive = args.archives_dir / CANDIDATES["vulkan"]["filename"]
    try:
        report = asyncio.run(run_matrix(
            archive,
            args.model_dir,
            args.voice_dir,
            args.release_manifest,
            args.output_dir,
            device=args.device,
            timeout=args.timeout,
            cancellation_delay=args.cancellation_delay,
        ))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Vulkan matrix failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
