import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qwen_vulkan_smoke", ROOT / "packaging/qwen_vulkan_smoke.py")
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)
MATRIX_SPEC = importlib.util.spec_from_file_location(
    "qwen_vulkan_matrix", ROOT / "packaging/qwen_vulkan_matrix.py"
)
MATRIX = importlib.util.module_from_spec(MATRIX_SPEC)
MATRIX_SPEC.loader.exec_module(MATRIX)


def evidence(mmproj="Vulkan0"):
    return f"load_tensors: Vulkan0 model buffer size = 811.93 MiB\nload_tensors: offloaded 29/29 layers to GPU\nclip_ctx: CLIP using {mmproj} backend\n"


def test_vulkan_matrix_has_one_fixed_preset_for_each_release_language():
    assert [item[0] for item in MATRIX.LANGUAGE_MATRIX] == ["zh", "en", "ja", "ko"]
    assert [item[1] for item in MATRIX.LANGUAGE_MATRIX] == [
        "zh_female_1", "en_female_1", "ja_female_1", "ko_female_1",
    ]
    assert all(item[2].strip() for item in MATRIX.LANGUAGE_MATRIX)


def test_vulkan_matrix_arguments_keep_backbone_and_mmproj_on_selected_device(tmp_path):
    voice = SimpleNamespace(reference_path=tmp_path / "voice.wav")
    arguments = MATRIX._arguments(
        "llama-tts", "model", "mmproj", "Vulkan2", voice, "ja", "text", "out.wav"
    )
    assert arguments[arguments.index("-dev") + 1] == "Vulkan2"
    assert arguments[arguments.index("-mmdev") + 1] == "Vulkan2"
    assert arguments[arguments.index("-ngl") + 1] == "all"
    assert arguments[arguments.index("--tts-lang") + 1] == "ja"


def test_vulkan_matrix_runs_languages_repeat_cancel_timeout_and_recovery(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model = model_dir / "model.gguf"
    mmproj = model_dir / "mmproj.gguf"
    model.write_bytes(b"model")
    mmproj.write_bytes(b"mmproj")
    release = tmp_path / "release.json"
    release.write_text(json.dumps({"model": {
        "filename": model.name, "sha256": "1" * 64,
        "mmproj_filename": mmproj.name, "mmproj_sha256": "2" * 64,
    }}), encoding="utf-8")
    voice = SimpleNamespace(id="fixture", reference_path=tmp_path / "voice.wav", sha256="3" * 64)
    calls = []

    async def run(arguments, runtime, log, timeout, *, profile="baseline"):
        calls.append((arguments, timeout))
        if "--list-devices" in arguments:
            log.write_text("Vulkan0: AMD test GPU", encoding="utf-8")
        elif len(calls) == 2:
            await asyncio.Future()
        else:
            raise TimeoutError("forced")

    async def synthesize(runtime, output_dir, **kwargs):
        return {
            "language": kwargs["language"], "voice": kwargs["voice"].id,
            "placement": {"mmproj_device": kwargs["device"]},
        }

    def digest(path):
        return "1" * 64 if path.name == model.name else "2" * 64

    output = tmp_path / "results"
    with patch.object(MATRIX.os, "name", "nt"), \
         patch.object(MATRIX, "audit_candidate", return_value={"archive_sha256": "a" * 64, "files": []}), \
         patch.object(MATRIX, "extract_runtime"), patch.object(MATRIX, "run_process", run), \
         patch.object(MATRIX, "_synthesize", synthesize), patch.object(MATRIX, "QwenVoiceCatalog") as catalog:
        catalog._sha256.side_effect = digest
        catalog.return_value.resolve.return_value = voice
        report = asyncio.run(MATRIX.run_matrix(
            tmp_path / "archive.zip", model_dir, tmp_path, release, output,
            cancellation_delay=0.05,
        ))

    assert report["state"] == "succeeded"
    assert [item["language"] for item in report["matrix"]] == ["zh", "en", "ja", "ko"]
    assert report["repeat"]["language"] == "zh"
    assert report["cancellation"] == {"attempted": True, "cancelled": True}
    assert report["timeout_recovery"]["timed_out"] is True
    assert report["timeout_recovery"]["recovered"] is True
    assert report["runtime_cleanup"] == "succeeded"
    assert json.loads((output / "report.json").read_text())["state"] == "succeeded"


def test_enumeration_requires_explicit_choice_for_multiple_devices():
    log = "Available devices:\n  Vulkan0: GPU one\n  Vulkan1: GPU two\n"
    assert SMOKE.select_device(log, "Vulkan1") == ("Vulkan1", "GPU two")
    with pytest.raises(RuntimeError, match="explicitly"):
        SMOKE.select_device(log, None)
    with pytest.raises(RuntimeError, match="not enumerated"):
        SMOKE.select_device(log, "CUDA0")
    assert SMOKE.select_device("Vulkan0: GPU one", None)[0] == "Vulkan0"
    with pytest.raises(RuntimeError):
        SMOKE.select_device("Available devices: (none)", None)


@pytest.mark.parametrize("log", ["", evidence("CPU"), evidence().replace("29/29", "0/29"),
                                 evidence().replace("29/29", "10/29"), evidence() + "CLIP using CPU backend"])
def test_vulkan_evidence_rejects_missing_partial_or_cpu_placement(log):
    with pytest.raises(RuntimeError, match="placement"):
        SMOKE.placement_evidence(log, "Vulkan0")


def test_hybrid_is_explicit_and_cannot_be_counted_as_full_vulkan():
    assert SMOKE.placement_evidence(evidence("CPU"), "Vulkan0", "cpu")["mmproj_device"] == "CPU"
    assert SMOKE.placement_evidence(evidence(), "Vulkan0")["offloaded_layers"] == 29


def test_transient_windows_dll_lock_is_retried():
    report = {}
    with patch.object(SMOKE.tempfile, "TemporaryDirectory") as factory, patch.object(SMOKE.time, "sleep"):
        factory.return_value.name = "temporary-runtime"
        factory.return_value.cleanup.side_effect = [PermissionError("locked DLL"), None]
        with SMOKE.temporary_runtime(report):
            pass
        assert factory.return_value.cleanup.call_count == 2
    assert report["runtime_cleanup"] == "succeeded"


def test_cleanup_failure_preserves_original_synthesis_error():
    report = {}
    with patch.object(SMOKE.tempfile, "TemporaryDirectory") as factory, patch.object(SMOKE.time, "sleep"):
        factory.return_value.name = "temporary-runtime"
        factory.return_value.cleanup.side_effect = PermissionError("locked DLL")
        with pytest.raises(RuntimeError, match="synthesis failed"):
            with SMOKE.temporary_runtime(report):
                raise RuntimeError("synthesis failed")
    assert report["runtime_cleanup"] == "failed"
    assert report["remaining_runtime_directory"] == "temporary-runtime"


def test_extraction_omits_server_rpc_and_checks_each_file(tmp_path):
    archive = tmp_path / "candidate.zip"
    names = ["llama-tts.exe", "ggml-vulkan.dll", "ggml-cpu-x64.dll", "llama-server.exe", "ggml-rpc.dll"]
    with zipfile.ZipFile(archive, "w") as output:
        for name in names:
            output.writestr(name, b"attested")
    inventory = {"files": [{"name": n, "sha256": hashlib.sha256(b"attested").hexdigest()} for n in names]}
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    SMOKE.extract_runtime(archive, runtime, inventory)
    assert {p.name for p in runtime.iterdir()} == set(names[:3])
    inventory["files"][0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="changed"):
        SMOKE.extract_runtime(archive, runtime, inventory)


class BlockedProcess:
    returncode = None

    def __init__(self):
        self.killed = False

    async def wait(self):
        if self.returncode is None:
            await asyncio.Future()
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.mark.parametrize("cancel", [False, True])
def test_process_timeout_and_async_cancellation_reap_child(tmp_path, cancel):
    async def exercise():
        process = BlockedProcess()
        started = asyncio.Event()

        async def factory(*args, **kwargs):
            assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
            assert kwargs["stdout"] is kwargs["stderr"]
            started.set()
            return process

        with patch.object(SMOKE.asyncio, "create_subprocess_exec", factory):
            task = asyncio.create_task(SMOKE.run_process(["candidate"], tmp_path, tmp_path / "log", 0.01))
            if cancel:
                await started.wait()
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                await task
        assert process.killed
        assert process.returncode is not None
    asyncio.run(exercise())


@pytest.mark.skipif(SMOKE.os.name != "nt", reason="Pinned Windows candidate")
@pytest.mark.parametrize("fail", [False, True])
def test_smoke_reports_outcome_and_removes_temporary_runtime(tmp_path, fail):
    release = json.loads((ROOT / "packaging/llama-tts/package-manifest.b10792.json").read_text())
    model = release["model"]
    for name in (model["filename"], model["mmproj_filename"]):
        (tmp_path / name).write_bytes(b"model fixture")
    voice = SimpleNamespace(reference_path=tmp_path / "voice.wav", sha256="a" * 64)
    runtimes = []

    async def run(arguments, runtime, log, timeout, *, profile="baseline"):
        runtimes.append(runtime)
        if "--list-devices" in arguments:
            log.write_text("Vulkan0: test GPU", encoding="utf-8")
        elif fail:
            log.write_text(evidence() + ASSERTION, encoding="utf-8")
            raise SMOKE.CandidateProcessError(3221226505, log.name)
        else:
            import wave
            log.write_text(evidence(), encoding="utf-8")
            with wave.open(str(runtime / "result.wav"), "wb") as wav:
                wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 2400)

    def model_hash(path):
        return model["sha256"] if path.name == model["filename"] else model["mmproj_sha256"]

    output = tmp_path / "results"
    with patch.object(SMOKE, "audit_candidate", return_value={"archive_sha256": "b" * 64}), \
         patch.object(SMOKE, "extract_runtime"), patch.object(SMOKE, "run_process", run), \
         patch.object(SMOKE, "QwenVoiceCatalog") as catalog:
        catalog._sha256.side_effect = model_hash
        catalog.return_value.resolve.return_value = voice
        if fail:
            with pytest.raises(RuntimeError, match="exited with code"):
                asyncio.run(SMOKE.run_smoke(tmp_path / "archive", tmp_path, output))
        else:
            asyncio.run(SMOKE.run_smoke(tmp_path / "archive", tmp_path, output))
    report = json.loads((output / "report.json").read_text())
    assert report["state"] == ("failed" if fail else "succeeded")
    if fail:
        assert report["failure"]["code"] == "vulkan_get_rows_alignment"
        assert report["placement"]["offloaded_layers"] == 29
    assert report["diagnostic_profile"] == "baseline"
    assert report["production_ready"] is False
    assert report["amd_hardware_validated"] is False
    assert bool((output / "result.wav").exists()) is not fail
    assert runtimes and all(not path.exists() for path in runtimes)


ASSERTION = "ggml-vulkan.cpp:12105: GGML_ASSERT(dst->op != GGML_OP_GET_ROWS || (a_offset == 0 && b_offset == 0 && d_offset == 0)) failed"


def test_known_assertion_requires_both_failed_process_and_exact_log_evidence():
    failed = SMOKE.CandidateProcessError(3221226505, "synthesis.log")
    assert SMOKE.failure_diagnostic(failed, ASSERTION)["code"] == "vulkan_get_rows_alignment"
    assert SMOKE.failure_diagnostic(failed, "GET_ROWS error")["code"] == "process_failed"
    assert SMOKE.failure_diagnostic(failed, ASSERTION.replace("ggml-vulkan", "ggml-cpu"))["code"] == "process_failed"
    assert SMOKE.failure_diagnostic(TimeoutError(), ASSERTION)["code"] == "timeout"
    assert SMOKE.failure_diagnostic(asyncio.CancelledError(), ASSERTION)["code"] == "cancelled"
    assert SMOKE.failure_diagnostic(RuntimeError("bad WAV"), ASSERTION)["code"] == "validation_or_runtime_error"


@pytest.mark.parametrize("profile", list(SMOKE.DIAGNOSTIC_PROFILES))
def test_diagnostic_profiles_are_applied_only_to_child(tmp_path, profile, monkeypatch):
    monkeypatch.setenv("GGML_VK_DISABLE_FUSION", "inherited")
    monkeypatch.setenv("LLAMA_ARG_DEVICE", "CPU")
    captured = []
    class Process:
        returncode = 0
        async def wait(self):
            return 0
    async def factory(*arguments, **kwargs):
        captured.append((arguments, kwargs))
        return Process()
    async def exercise():
        with patch.object(SMOKE.asyncio, "create_subprocess_exec", factory):
            await SMOKE.run_process(["candidate", "-p", "test"], tmp_path, tmp_path / "synthesis.log", 1, profile=profile)
            await SMOKE.run_process(["candidate", "--list-devices"], tmp_path, tmp_path / "devices.log", 1, profile=profile)
    asyncio.run(exercise())
    arguments, options = captured[0]
    assert ("--no-op-offload" in arguments) == (profile == "no-op-offload")
    assert "--no-op-offload" not in captured[1][0]
    assert "LLAMA_ARG_DEVICE" not in options["env"]
    assert options["env"].get("GGML_VK_DISABLE_FUSION") == ("1" if profile == "no-vulkan-optimizations" else None)
    assert SMOKE.os.environ["GGML_VK_DISABLE_FUSION"] == "inherited"
