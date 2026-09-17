import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

from app.services.tts.base import TTSCancelled
from app.workers.llama_tts_worker import (
    LlamaTTSArtifactError, LlamaTTSArtifactSet, LlamaTTSWorkerClient,
    select_amd_vulkan_device,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_wav(path: Path, *, frames: int = 2400) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\0\0" * frames)


def make_bundle(root: Path) -> dict:
    runtime, voices = root / "runtime", root / "voices"
    runtime.mkdir()
    voices.mkdir()
    executable, dependency = runtime / "llama-tts.exe", runtime / "ggml.dll"
    model, mmproj = root / "model.gguf", root / "mmproj.gguf"
    executable.write_bytes(b"reviewed executable")
    dependency.write_bytes(b"reviewed dependency")
    model.write_bytes(b"reviewed model")
    mmproj.write_bytes(b"reviewed mmproj")
    voice = voices / "zh_female_1.wav"
    write_wav(voice)
    (voices / "SOURCES.json").write_text(json.dumps({"voices": [{
        "id": "zh_female_1", "language": "Chinese", "voice_presentation": "female",
        "file": voice.name, "sha256": sha256(voice),
    }]}), encoding="utf-8")
    manifest = runtime / "runtime.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "runtime_version": "b10792-c5a5535e6",
        "executable": executable.name,
        "files": {executable.name: sha256(executable), dependency.name: sha256(dependency)},
    }), encoding="utf-8")
    return {
        "runtime_root": str(runtime), "runtime_manifest": str(manifest),
        "model_path": str(model), "model_sha256": sha256(model),
        "mmproj_path": str(mmproj), "mmproj_sha256": sha256(mmproj),
        "voice_catalog_root": str(voices), "device": "cuda",
    }


class FakeProcess:
    def __init__(self, *, blocked=False, returncode=None):
        self.returncode, self.blocked, self.killed = returncode, blocked, False

    async def wait(self):
        if self.blocked and self.returncode is None:
            await asyncio.Future()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.killed, self.blocked, self.returncode = True, False, -9


class LlamaTTSArtifactTests(unittest.TestCase):
    def test_accepts_hash_pinned_runtime_and_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = LlamaTTSArtifactSet.load(make_bundle(Path(temp)))
            self.assertEqual(artifacts.runtime_version, "b10792-c5a5535e6")
            self.assertEqual(artifacts.executable.name, "llama-tts.exe")

    def test_rejects_runtime_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = make_bundle(Path(temp))
            Path(config["runtime_root"], "ggml.dll").write_bytes(b"tampered")
            with self.assertRaisesRegex(LlamaTTSArtifactError, "checksum mismatch"):
                LlamaTTSArtifactSet.load(config)

    def test_rejects_escaping_runtime_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = make_bundle(Path(temp))
            manifest_path = Path(config["runtime_manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["../outside.dll"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(LlamaTTSArtifactError, "artifact path"):
                LlamaTTSArtifactSet.load(config)

    def test_vulkan_enumeration_selects_the_only_amd_device(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = LlamaTTSArtifactSet.load(make_bundle(Path(temp)))
            result = SimpleNamespace(
                returncode=0,
                stdout="Vulkan0: Intel Graphics\nVulkan1: AMD Radeon RX 5700 XT\n",
                stderr="",
            )
            with patch("app.workers.llama_tts_worker.subprocess.run", return_value=result):
                with self.assertLogs("app.workers.llama_tts_worker", level="INFO") as logs:
                    self.assertEqual(select_amd_vulkan_device(artifacts), "Vulkan1")
            self.assertIn(
                "Qwen3-TTS selected Vulkan device: Vulkan1 / AMD Radeon RX 5700 XT",
                logs.output[0],
            )

    def test_vulkan_enumeration_rejects_ambiguous_amd_devices(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            artifacts = LlamaTTSArtifactSet.load(make_bundle(Path(temp)))
            result = SimpleNamespace(
                returncode=0,
                stdout="Vulkan0: AMD Radeon A\nVulkan1: AMD Radeon B\n",
                stderr="",
            )
            with patch("app.workers.llama_tts_worker.subprocess.run", return_value=result):
                with self.assertRaisesRegex(LlamaTTSArtifactError, "exactly one"):
                    select_amd_vulkan_device(artifacts)


class LlamaTTSWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_vulkan_enumeration_failure_falls_back_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            config, calls = make_bundle(Path(temp)), []
            config["device"] = "vulkan"

            def reject(_artifacts):
                raise LlamaTTSArtifactError("Vulkan driver unavailable")

            async def factory(*arguments, **kwargs):
                calls.append(arguments)
                write_wav(Path(arguments[arguments.index("-o") + 1]))
                return FakeProcess(returncode=0)

            client = LlamaTTSWorkerClient(
                process_factory=factory, vulkan_device_selector=reject
            )
            await client.start(config)
            self.assertEqual(client.status["requested_device"], "vulkan")
            await client.synthesize(text="test", voice_id="zh_female_1", language="zh")
            self.assertIn("--no-op-offload", calls[0])
            self.assertEqual(client.status["actual_device"], "cpu")
            self.assertEqual(client.status["fallback_reason"], "Vulkan driver unavailable")

    async def test_vulkan_uses_enumerated_device_for_backbone_and_mmproj(self):
        with tempfile.TemporaryDirectory() as temp:
            config, calls = make_bundle(Path(temp)), []
            config["device"] = "vulkan"

            async def factory(*arguments, **kwargs):
                calls.append(arguments)
                write_wav(Path(arguments[arguments.index("-o") + 1]))
                return FakeProcess(returncode=0)

            client = LlamaTTSWorkerClient(
                process_factory=factory,
                vulkan_device_selector=lambda _artifacts: "Vulkan3",
            )
            await client.start(config)
            await client.synthesize(text="test", voice_id="zh_female_1", language="zh")
            self.assertEqual(calls[0][calls[0].index("-dev") + 1], "Vulkan3")
            self.assertEqual(calls[0][calls[0].index("-mmdev") + 1], "Vulkan3")
            self.assertEqual(client.status["actual_device"], "vulkan")

    async def test_vulkan_failure_retries_once_on_cpu(self):
        with tempfile.TemporaryDirectory() as temp:
            config, calls = make_bundle(Path(temp)), []
            config["device"] = "vulkan"

            async def factory(*arguments, **kwargs):
                calls.append(arguments)
                if "Vulkan0" in arguments:
                    return FakeProcess(returncode=7)
                write_wav(Path(arguments[arguments.index("-o") + 1]))
                return FakeProcess(returncode=0)

            client = LlamaTTSWorkerClient(
                process_factory=factory,
                vulkan_device_selector=lambda _artifacts: "Vulkan0",
            )
            await client.start(config)
            await client.synthesize(text="test", voice_id="zh_female_1", language="zh")
            self.assertEqual(len(calls), 2)
            self.assertIn("--no-op-offload", calls[1])
            self.assertEqual(client.status["actual_device"], "cpu")
            self.assertIn("exited with code 7", client.status["fallback_reason"])

    async def test_cancel_before_launch_reports_cancelled_and_can_recover(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            async def factory(*args, **kwargs):
                calls.append(args)
                write_wav(Path(args[args.index("-o") + 1]))
                return FakeProcess(returncode=0)
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            with self.assertRaises(TTSCancelled):
                await client.synthesize(text="test", voice_id="zh_female_1", language="zh", should_cancel=lambda: True)
            self.assertEqual(client.status["state"], "cancelled")
            self.assertEqual(calls, [])
            self.assertIsNone(client.status["actual_device"])
            await client.synthesize(text="test", voice_id="zh_female_1", language="zh")
            self.assertEqual(client.status["state"], "succeeded")

    async def test_cancel_on_retry_transition_does_not_leave_retrying_status(self):
        with tempfile.TemporaryDirectory() as temp:
            calls, statuses = [], []
            async def factory(*args, **kwargs):
                calls.append(args)
                return FakeProcess(returncode=1)
            client = LlamaTTSWorkerClient(process_factory=factory, status_callback=statuses.append)
            await client.start(make_bundle(Path(temp)))
            with self.assertRaises(TTSCancelled):
                await client.synthesize(text="test", voice_id="zh_female_1", language="zh",
                                        should_cancel=lambda: client.status["state"] == "retrying")
            self.assertEqual(len(calls), 1)
            self.assertEqual(statuses[-1]["state"], "cancelled")
            self.assertTrue(statuses[-1]["fallback_reason"])
            self.assertFalse(client.running)

    async def test_cancel_during_process_creation_reports_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            entered = asyncio.Event()
            directories = []
            async def factory(*args, **kwargs):
                directories.append(Path(kwargs["cwd"]))
                entered.set()
                await asyncio.Future()
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            task = asyncio.create_task(client.synthesize(text="test", voice_id="zh_female_1", language="zh"))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(client.status["state"], "cancelled")
            self.assertIsNone(client.status["fallback_reason"])
            self.assertFalse(client.running)
            self.assertTrue(all(not path.exists() for path in directories))

    async def test_fallback_policy_matrix(self):
        for failure in ("exit", "spawn", "wav"):
            for enabled in (True, False):
                with self.subTest(failure=failure, enabled=enabled), tempfile.TemporaryDirectory() as temp:
                    config = make_bundle(Path(temp))
                    config["auto_cpu_fallback"] = enabled
                    calls = []
                    async def factory(*args, **kwargs):
                        calls.append((args, kwargs))
                        if "CUDA0" in args:
                            if failure == "spawn":
                                raise OSError(5, "cannot initialize")
                            return FakeProcess(returncode=1 if failure == "exit" else 0)
                        write_wav(Path(args[args.index("-o") + 1]))
                        return FakeProcess(returncode=0)
                    client = LlamaTTSWorkerClient(process_factory=factory)
                    await client.start(config)
                    if enabled:
                        for _ in range(2):
                            audio = await client.synthesize(text="test", voice_id="zh_female_1", language="zh")
                            self.assertTrue(audio.startswith(b"RIFF"))
                        self.assertEqual(len(calls), 3)
                        self.assertEqual(client.status["actual_device"], "cpu")
                        self.assertEqual(client.status["state"], "succeeded")
                        self.assertTrue(client.status["fallback_reason"])
                        self.assertIn("--no-op-offload", calls[1][0])
                    else:
                        with self.assertRaises(RuntimeError):
                            await client.synthesize(text="test", voice_id="zh_female_1", language="zh")
                        self.assertEqual(len(calls), 1)
                    self.assertTrue(all(not Path(kw["cwd"]).exists() for _, kw in calls))
                    await client.close()

    async def test_cpu_failure_does_not_loop(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            async def factory(*args, **kwargs):
                calls.append(args)
                return FakeProcess(returncode=7)
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            with self.assertRaisesRegex(RuntimeError, "CUDA failed:.*CPU retry failed"):
                await client.synthesize(text="test", voice_id="zh_female_1", language="zh")
            self.assertEqual(len(calls), 2)
            self.assertFalse(client.running)
            self.assertEqual(client.status["state"], "failed")

    async def test_cpu_retry_async_cancellation_reaps_process(self):
        with tempfile.TemporaryDirectory() as temp:
            started = asyncio.Event()
            process = FakeProcess(blocked=True)
            async def factory(*args, **kwargs):
                if "CUDA0" in args:
                    return FakeProcess(returncode=1)
                started.set()
                return process
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            task = asyncio.create_task(client.synthesize(text="test", voice_id="zh_female_1", language="zh"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(process.killed)
            self.assertFalse(client.running)
            self.assertEqual(client.status["state"], "cancelled")

    async def test_cancel_between_attempts_prevents_cpu_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            async def factory(*args, **kwargs):
                calls.append(args)
                return FakeProcess(returncode=1)
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            with self.assertRaises(TTSCancelled):
                await client.synthesize(text="test", voice_id="zh_female_1", language="zh", should_cancel=lambda: bool(calls))
            self.assertEqual(len(calls), 1)

    async def test_uses_fixed_local_arguments_and_returns_valid_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config, calls = make_bundle(Path(temp)), []

            async def factory(*arguments, **kwargs):
                calls.append((arguments, kwargs))
                write_wav(Path(arguments[arguments.index("-o") + 1]))
                return FakeProcess(returncode=0)

            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(config)
            audio = await client.synthesize(text="你好，这是测试。", voice_id="zh_female_1", language="zh")
            await client.close()

            arguments, kwargs = calls[0]
            self.assertTrue(audio.startswith(b"RIFF"))
            self.assertIn("--tts-speaker-file", arguments)
            self.assertIn("CUDA0", arguments)
            self.assertNotIn("-hf", arguments)
            self.assertNotIn("--url", arguments)
            self.assertNotIn("--rpc", arguments)
            self.assertIsNotNone(kwargs["stdin"])

    async def test_cancellation_kills_and_reaps_active_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            process = FakeProcess(blocked=True)

            async def factory(*arguments, **kwargs):
                return process

            checks = 0

            def cancelled():
                nonlocal checks
                checks += 1
                return checks > 1

            client = LlamaTTSWorkerClient(process_factory=factory, poll_interval=0.001)
            await client.start(make_bundle(Path(temp)))
            with self.assertRaisesRegex(TTSCancelled, "process recycled"):
                await client.synthesize(
                    text="取消测试", voice_id="zh_female_1", language="zh", should_cancel=cancelled
                )
            self.assertTrue(process.killed)
            self.assertFalse(client.running)

    async def test_timeout_kills_and_reaps_active_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            process = FakeProcess(blocked=True)

            async def factory(*arguments, **kwargs):
                return process

            client = LlamaTTSWorkerClient(process_factory=factory, poll_interval=0.001)
            await client.start(make_bundle(Path(temp)))
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                await client.synthesize(
                    text="超时测试", voice_id="zh_female_1", language="zh", timeout=0.003
                )
            self.assertTrue(process.killed)
            self.assertFalse(client.running)

    async def test_invalid_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            async def factory(*arguments, **kwargs):
                Path(arguments[arguments.index("-o") + 1]).write_bytes(b"not wav")
                return FakeProcess(returncode=0)

            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            with self.assertRaisesRegex(RuntimeError, "invalid WAV"):
                await client.synthesize(text="无效输出", voice_id="zh_female_1", language="zh")


if __name__ == "__main__":
    unittest.main()
