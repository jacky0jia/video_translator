import unittest
import tempfile
import json
import io
import threading
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.core.provider_lifecycle import NoopProviderLifecycle
from app.services.asr_service import ASRService
from app.services.dubbing_service import DubbingCancelled, DubbingService
from app.services.tts.audio import normalized_wav_result
from app.services.tts.base import VoiceInfo
from app.services.tts.registry import DEFAULT_TTS_REGISTRY, TTSProviderRegistry
from app.services.translation_service import TranslationService, settings as translation_settings


class FakeProviderLifecycle:
    """Record lifecycle boundaries without loading GPU models."""

    def __init__(self):
        self.events = []

    async def startup(self, *, task_id, stage, provider):
        self.events.append(("startup", task_id, stage, provider))

    async def shutdown(self, *, task_id, stage, provider):
        self.events.append(("shutdown", task_id, stage, provider))


class FakeResponse:
    def __init__(self, translations):
        self._translations = translations

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": json.dumps({"translations": self._translations})}}]
        }


class RecordingHttpClient:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        count = len(kwargs["json"]["messages"][1]["content"].splitlines())
        return FakeResponse(["translated"] * max(count, 1))


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        audit = patch.object(TranslationService, "_audit_batch_alignment", new_callable=AsyncMock, return_value=[])
        audit.start()
        self.addCleanup(audit.stop)

    def test_empty_api_key_omits_authorization_header(self):
        self.assertEqual(TranslationService._auth_headers(""), {})
        self.assertEqual(
            TranslationService._auth_headers(" secret "),
            {"Authorization": "Bearer secret"},
        )

    async def test_tts_adapters_default_to_noop_lifecycle(self):
        service = DubbingService()

        self.assertIsInstance(service._lifecycle, NoopProviderLifecycle)
        self.assertEqual(
            {service.tts_registry.create(provider_id).id for provider_id in ("kokoro", "edge", "speaches")},
            {"kokoro", "edge", "speaches"},
        )

    async def test_asr_stage_exposes_injectable_lifecycle(self):
        lifecycle = FakeProviderLifecycle()
        service = ASRService(lifecycle=lifecycle, model_factory=lambda *a, **k: None)

        with patch("app.services.asr_service.settings.ASR_API_URL", ""):
            async with service.stage("task-asr"):
                pass

        self.assertEqual(
            lifecycle.events,
            [
                ("startup", "task-asr", "asr", "faster_whisper"),
                ("shutdown", "task-asr", "asr", "faster_whisper"),
            ],
        )

    async def test_asr_stage_unloads_native_model_before_releasing_gpu(self):
        events = []

        class Engine:
            def unload_model(self):
                events.append("native_unload")

        class Model:
            model = Engine()

        class Lifecycle(FakeProviderLifecycle):
            async def shutdown(self, **metadata):
                events.append("lease_release")
                await super().shutdown(**metadata)

        service = ASRService(lifecycle=Lifecycle(), model_factory=lambda *a, **k: Model())
        with patch("app.services.asr_service.settings.ASR_API_URL", ""), patch.object(
            service, "_gpu_memory_snapshot", return_value=100
        ):
            async with service.stage("task-asr"):
                service.model = Model()

        self.assertIsNone(service.model)
        self.assertEqual(events, ["native_unload", "lease_release"])

    async def test_asr_stage_exception_still_unloads_model(self):
        class Engine:
            unloaded = False

            def unload_model(self):
                self.unloaded = True

        class Model:
            def __init__(self):
                self.model = Engine()

        service = ASRService(model_factory=lambda *a, **k: Model())
        model = Model()
        with patch("app.services.asr_service.settings.ASR_API_URL", ""), patch.object(
            service, "_gpu_memory_snapshot", return_value=None
        ), self.assertRaisesRegex(RuntimeError, "asr failed"):
            async with service.stage("task-asr"):
                service.model = model
                raise RuntimeError("asr failed")

        self.assertTrue(model.model.unloaded)
        self.assertIsNone(service.model)

    async def test_asr_worker_is_shared_for_stage_and_closed(self):
        class Worker:
            def __init__(self):
                self.started = 0
                self.calls = []
                self.closed = 0

            async def start(self, **kwargs):
                self.started += 1

            async def transcribe(self, audio_path, language):
                self.calls.append((audio_path, language))
                return TranscriptionResult(video_source=str(audio_path), language="en", segments=[])

            async def close(self):
                self.closed += 1

        worker = Worker()
        service = ASRService(worker_factory=lambda: worker)
        with patch("app.services.asr_service.settings.ASR_API_URL", ""), patch.object(
            service, "_resolve_device_and_compute", return_value=("cuda", "float16")
        ), patch.object(service, "_resolve_model_path", return_value="fixture-model"), patch.object(
            service, "_gpu_memory_snapshot", return_value=None
        ):
            async with service.stage("task-worker"):
                await service.transcribe(Path("one.wav"))
                await service.transcribe(Path("two.wav"), "en")

        self.assertEqual(worker.started, 1)
        self.assertEqual(worker.calls, [(Path("one.wav"), None), (Path("two.wav"), "en")])
        self.assertEqual(worker.closed, 1)
        self.assertIsNone(service._worker)

    async def test_asr_worker_is_closed_when_stage_fails(self):
        class Worker:
            closed = False

            async def start(self, **kwargs):
                pass

            async def close(self):
                self.closed = True

        worker = Worker()
        service = ASRService(worker_factory=lambda: worker)
        with patch("app.services.asr_service.settings.ASR_API_URL", ""), patch.object(
            service, "_resolve_device_and_compute", return_value=("cuda", "float16")
        ), patch.object(service, "_resolve_model_path", return_value="fixture-model"), patch.object(
            service, "_gpu_memory_snapshot", return_value=None
        ), self.assertRaisesRegex(RuntimeError, "pipeline failed"):
            async with service.stage("task-worker"):
                raise RuntimeError("pipeline failed")

        self.assertTrue(worker.closed)
        self.assertIsNone(service._worker)

    async def test_translation_lifecycle_wraps_all_batches_and_cleans_up(self):
        lifecycle = FakeProviderLifecycle()
        service = TranslationService(lifecycle=lifecycle, provider_name="fake_ollama")
        calls = []

        async def fake_translate(current, previous, following, target):
            calls.append([segment.text for segment in current])
            return [f"translated:{segment.text}" for segment in current]

        service.translate_batch = fake_translate
        result = TranscriptionResult(
            video_source=str(Path("fixture.wav")),
            language="en",
            segments=[
                TranscriptionSegment(start=0, end=1, text="one", confidence=1),
                TranscriptionSegment(start=1, end=2, text="two", confidence=1),
                TranscriptionSegment(start=2, end=3, text="three", confidence=1),
            ],
        )

        translated = await service.translate_full_result(
            result, target_lang="Chinese", batch_size=2, task_id="task-llm"
        )

        self.assertEqual(calls, [["one", "two"], ["three"]])
        self.assertEqual([segment.text for segment in translated.segments], [
            "translated:one", "translated:two", "translated:three"
        ])
        self.assertEqual(
            lifecycle.events,
            [
                ("startup", "task-llm", "translation", "fake_ollama"),
                ("shutdown", "task-llm", "translation", "fake_ollama"),
            ],
        )

    async def test_translation_failure_still_runs_shutdown(self):
        lifecycle = FakeProviderLifecycle()
        service = TranslationService(lifecycle=lifecycle)

        async def fail(*args, **kwargs):
            raise RuntimeError("simulated provider failure")

        service.translate_batch = fail
        result = TranscriptionResult(
            video_source="fixture.wav",
            language="en",
            segments=[TranscriptionSegment(start=0, end=1, text="one", confidence=1)],
        )

        with self.assertRaisesRegex(RuntimeError, "simulated provider failure"):
            await service.translate_full_result(result, task_id="task-failure")

        self.assertEqual(lifecycle.events[-1][0], "shutdown")

    async def test_empty_dubbing_does_not_start_provider_lifecycle(self):
        lifecycle = FakeProviderLifecycle()
        service = DubbingService(lifecycle=lifecycle)

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.dubbing_service.settings.TTS_MODE", "local"
        ):
            paths = await service._synthesize_all(
                [], "af_heart", 1.0, "task-tts", Path(temp_dir)
            )

        self.assertEqual(paths, [])
        # An empty run, like a fully cached run, must not load a model.
        self.assertEqual(lifecycle.events, [])

    async def test_qwen_dubbing_uses_dedicated_gpu_lifecycle(self):
        ordinary = FakeProviderLifecycle()
        gpu = FakeProviderLifecycle()
        registry = TTSProviderRegistry(DEFAULT_TTS_REGISTRY.list())

        class QwenProvider:
            id = "qwen"

            async def list_voices(self):
                return []

            async def synthesize(self, request, should_cancel=None):
                buffer = io.BytesIO()
                with wave.open(buffer, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(request.sample_rate)
                    wav.writeframes(b"\x00\x00" * 100)
                return normalized_wav_result(buffer.getvalue(), request.sample_rate)

            async def close(self):
                pass

        registry.register_provider(QwenProvider())
        service = DubbingService(
            lifecycle=ordinary, gpu_lifecycle=gpu, tts_registry=registry
        )
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.dubbing_service.settings.TEMP_DIR", Path(temp_dir) / "cache"
        ):
            paths = await service._synthesize_all(
                [TranscriptionSegment(start=0, end=1, text="hello", confidence=1)],
                "zh_female_1", 1.0, "task-qwen", Path(temp_dir), "qwen", "zh"
            )
        self.assertEqual(len(paths), 1)
        self.assertEqual(ordinary.events, [])
        self.assertEqual(gpu.events[0], ("startup", "task-qwen", "tts", "qwen"))
        self.assertEqual(gpu.events[-1], ("shutdown", "task-qwen", "tts", "qwen"))

    async def test_dubbing_cancellation_stops_later_segments_and_shuts_down(self):
        lifecycle = FakeProviderLifecycle()
        registry = TTSProviderRegistry(DEFAULT_TTS_REGISTRY.list())
        calls = []
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x00\x00" * 100)
        audio = buffer.getvalue()
        cancel_event = threading.Event()

        class CancellingProvider:
            id = "kokoro"
            closed = False

            async def list_voices(self):
                return [VoiceInfo("af_heart", provider="kokoro", language="en-us")]

            async def synthesize(self, request, should_cancel=None):
                calls.append(request.text)
                cancel_event.set()
                return normalized_wav_result(audio, request.sample_rate)

            async def close(self):
                self.closed = True

        provider = CancellingProvider()
        registry.register_provider(provider)
        service = DubbingService(lifecycle=lifecycle, tts_registry=registry)
        service._cancel_events["task-cancel"] = cancel_event
        segments = [
            TranscriptionSegment(start=0, end=1, text="one", confidence=1),
            TranscriptionSegment(start=1, end=2, text="two", confidence=1),
        ]

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "app.services.dubbing_service.settings.TEMP_DIR", Path(temp_dir) / "cache"
        ):
            with self.assertRaises(DubbingCancelled):
                await service._synthesize_all(
                    segments,
                    "af_heart",
                    1.0,
                    "task-cancel",
                    Path(temp_dir),
                    "kokoro",
                )
            self.assertEqual(calls, ["one"])
            # Cancellation after synthesis must not publish or cache the clip.
            self.assertEqual(len(list(Path(temp_dir).glob("raw_*.wav"))), 0)
            self.assertEqual(len(list((Path(temp_dir) / "cache").rglob("*.wav"))), 0)
        self.assertEqual(lifecycle.events[0][0], "startup")
        self.assertEqual(lifecycle.events[-1][0], "shutdown")
        self.assertTrue(provider.closed)

    async def test_translation_reads_model_changed_after_service_creation(self):
        RecordingHttpClient.calls = []
        with patch("app.services.translation_service.settings.LLM_PROVIDER", "openai_compatible"), patch("app.services.translation_service.settings.LLM_API_BASE_URL", "http://127.0.0.1:11434/v1/"), patch(
            "app.services.translation_service.settings.LLM_MODEL_NAME", "old-model"
        ):
            service = TranslationService(http_client_factory=RecordingHttpClient)
            with patch(
                "app.services.translation_service.settings.LLM_MODEL_NAME", "new-model"
            ):
                await service.translate_batch([
                    TranscriptionSegment(start=0, end=1, text="one", confidence=1)
                ])

        url, request = RecordingHttpClient.calls[0]
        self.assertEqual(url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(request["json"]["model"], "new-model")

    async def test_lm_studio_uses_json_schema_response_format(self):
        RecordingHttpClient.calls = []
        with patch("app.services.translation_service.settings.LLM_PROVIDER", "lm_studio"), patch(
            "app.services.translation_service.settings.LM_STUDIO_MODEL", "gemma"
        ):
            service = TranslationService(http_client_factory=RecordingHttpClient)
            await service.translate_batch([
                TranscriptionSegment(start=0, end=1, text="one", confidence=1),
                TranscriptionSegment(start=1, end=2, text="two", confidence=1),
            ])

        response_format = RecordingHttpClient.calls[0][1]["json"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        schema = response_format["json_schema"]["schema"]
        self.assertEqual(schema["required"], ["translations"])
        self.assertEqual(schema["properties"]["translations"]["minItems"], 2)
        self.assertEqual(schema["properties"]["translations"]["maxItems"], 2)
        self.assertEqual(RecordingHttpClient.calls[0][1]["json"]["max_tokens"], 320)
        self.assertEqual(
            RecordingHttpClient.calls[0][1]["json"]["reasoning_effort"], "none"
        )

        compression_format = service._response_format(
            "lm_studio", key="translation"
        )
        compression_schema = compression_format["json_schema"]["schema"]
        self.assertEqual(
            compression_schema["properties"]["translation"], {"type": "string"}
        )

    async def test_openai_compatible_keeps_json_object_response_format(self):
        RecordingHttpClient.calls = []
        with patch("app.services.translation_service.settings.LLM_PROVIDER", "openai_compatible"):
            service = TranslationService(http_client_factory=RecordingHttpClient)
            await service.translate_batch([
                TranscriptionSegment(start=0, end=1, text="one", confidence=1)
            ])

        response_format = RecordingHttpClient.calls[0][1]["json"]["response_format"]
        self.assertEqual(response_format, {"type": "json_object"})

    async def test_translation_task_keeps_one_model_for_all_batches(self):
        service = TranslationService()
        seen_models = []

        async def record_model(current, previous, following, target):
            seen_models.append(service._task_config.get().model)
            if len(seen_models) == 1:
                translation_settings.LLM_MODEL_NAME = "changed-mid-task"
            return [f"translated:{segment.text}" for segment in current]

        service.translate_batch = record_model
        result = TranscriptionResult(
            video_source="fixture.wav",
            language="en",
            segments=[
                TranscriptionSegment(start=0, end=1, text="one", confidence=1),
                TranscriptionSegment(start=1, end=2, text="two", confidence=1),
            ],
        )
        with patch("app.services.translation_service.settings.LLM_PROVIDER", "openai_compatible"), patch(
            "app.services.translation_service.settings.LLM_MODEL_NAME", "selected-model"
        ):
            await service.translate_full_result(result, batch_size=1)

        self.assertEqual(seen_models, ["selected-model", "selected-model"])

    async def test_ollama_task_snapshots_selected_model(self):
        service = TranslationService()
        seen_models = []

        async def record_model(current, previous, following, target):
            seen_models.append(service._task_config.get().model)
            translation_settings.OLLAMA_MODEL = "changed-mid-task"
            return ["translated"]

        service.translate_batch = record_model
        result = TranscriptionResult(
            video_source="fixture.wav", language="en",
            segments=[TranscriptionSegment(start=0, end=1, text="one", confidence=1)],
        )
        with patch("app.services.translation_service.settings.LLM_PROVIDER", "ollama"), patch(
            "app.services.translation_service.settings.OLLAMA_MODEL", "selected-ollama"
        ):
            await service.translate_full_result(result, batch_size=1)

        self.assertEqual(seen_models, ["selected-ollama"])

    async def test_mismatched_batch_is_retried_without_shifting_rows(self):
        service = TranslationService()
        calls = []

        async def mismatch_then_singles(current, previous, following, target):
            calls.append([segment.text for segment in current])
            if len(current) > 1:
                # Simulate a model omitting the first translation. The old
                # behavior padded the end and shifted every remaining row.
                return ["译文二", "译文三"]
            return [f"译:{current[0].text}"]

        service.translate_batch = mismatch_then_singles
        result = TranscriptionResult(
            video_source="fixture.wav",
            language="en",
            segments=[
                TranscriptionSegment(start=0, end=1, text="one", confidence=1),
                TranscriptionSegment(start=1, end=2, text="two", confidence=1),
                TranscriptionSegment(start=2, end=3, text="three", confidence=1),
            ],
        )

        translated = await service.translate_full_result(result, batch_size=3)

        self.assertEqual(
            calls,
            [["one", "two", "three"], ["one"], ["two"], ["three"]],
        )
        self.assertEqual(
            [segment.text for segment in translated.segments],
            ["译:one", "译:two", "译:three"],
        )


if __name__ == "__main__":
    unittest.main()
