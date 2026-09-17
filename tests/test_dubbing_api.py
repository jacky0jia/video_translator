import asyncio
import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app.api.dubbing import DubbingRequest, list_voices, start_dubbing
from app.api.pipeline import PipelineRequest, start_pipeline
from app.services.tts.base import VoiceInfo
from app.services.tts.registry import TTSRoute
from app.services.tts.voice_selection import resolve_available_voice


class DubbingApiTests(unittest.TestCase):
    def test_edge_accepts_four_languages_and_corrects_mismatched_voice(self):
        defaults = {
            "Chinese": "zh-CN-XiaoxiaoNeural",
            "English": "en-US-JennyNeural",
            "Japanese": "ja-JP-NanamiNeural",
            "Korean": "ko-KR-SunHiNeural",
        }
        task = {
            "task_id": "task-1", "language": "English",
            "upload_path": "fixture.mp4", "transcription_path": "fixture.json",
        }
        for language, expected_voice in defaults.items():
            background_tasks = BackgroundTasks()
            with self.subTest(language=language), patch(
                "app.api.dubbing.history_manager.get_task", return_value=task
            ), patch("app.api.dubbing.Path.exists", return_value=True), patch(
                "app.api.dubbing.settings.TTS_MODE", "edge"
            ):
                result = asyncio.run(start_dubbing(DubbingRequest(
                    task_id="task-1", target_lang=language,
                    voice="ko-KR-SunHiNeural" if language != "Korean" else "af_heart",
                ), background_tasks))
            self.assertEqual(result["status"], "started")
            self.assertEqual(background_tasks.tasks[0].args[2], expected_voice)

    def test_pipeline_rejects_non_default_cosyvoice_generation_speed(self):
        with patch("app.api.pipeline.history_manager.get_task", return_value={"task_id": "task-1"}), patch(
            "app.api.pipeline.settings.TTS_MODE", "cosyvoice"
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(start_pipeline(PipelineRequest(
                    task_id="task-1", target_lang="Chinese", speed=1.25
                )))
        self.assertIn("speed must be 1.0", raised.exception.detail)

    def test_cosyvoice_requires_fixed_speed_and_rejects_clone_upload(self):
        task = {
            "task_id": "task-1", "language": "Chinese",
            "upload_path": "fixture.mp4", "transcription_path": "fixture.json",
        }
        with patch("app.api.dubbing.history_manager.get_task", return_value=task), patch(
            "app.api.dubbing.Path.exists", return_value=True
        ), patch("app.api.dubbing.settings.TTS_MODE", "cosyvoice"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(start_dubbing(DubbingRequest(
                    task_id="task-1", target_lang="source", voice="zh_female_1", speed=1.1
                ), BackgroundTasks()))
        self.assertIn("speed must be 1.0", raised.exception.detail)

        with patch("app.api.dubbing.history_manager.get_task", return_value=task), patch(
            "app.api.dubbing.Path.exists", return_value=True
        ), patch("app.api.dubbing.settings.TTS_MODE", "cosyvoice"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(start_dubbing(DubbingRequest(
                    task_id="task-1", target_lang="source", voice="zh_female_1",
                    clone_sample_path="uploaded.wav"
                ), BackgroundTasks()))
        self.assertIn("预置音色", raised.exception.detail)

    def test_kokoro_rejects_speed_above_engine_limit(self):
        task = {
            "task_id": "task-1", "language": "English",
            "upload_path": "fixture.mp4", "transcription_path": "fixture.json",
        }
        background_tasks = BackgroundTasks()
        with patch("app.api.dubbing.history_manager.get_task", return_value=task), patch(
            "app.api.dubbing.Path.exists", return_value=True
        ), patch("app.api.dubbing.settings.TTS_MODE", "kokoro"):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(start_dubbing(DubbingRequest(
                    task_id="task-1", target_lang="source", voice="af_heart", speed=2.1
                ), background_tasks))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Kokoro speed", raised.exception.detail)
        self.assertEqual(background_tasks.tasks, [])

    def test_kokoro_rejects_korean_without_silently_using_edge(self):
        task = {
            "task_id": "task-1",
            "language": "English",
            "target_lang": "Korean",
            "upload_path": "fixture.mp4",
            "transcription_path": "fixture.json",
            "translations": {"Korean": "fixture-ko.json"},
        }
        background_tasks = BackgroundTasks()

        with patch(
            "app.api.dubbing.history_manager.get_task", return_value=task
        ), patch("app.api.dubbing.Path.exists", return_value=True), patch(
            "app.api.dubbing.settings.TTS_MODE", "kokoro"
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(start_dubbing(
                    DubbingRequest(
                        task_id="task-1",
                        target_lang="Korean",
                        voice="af_heart",
                    ),
                    background_tasks,
                ))

        self.assertIn("Kokoro", raised.exception.detail)
        self.assertEqual(background_tasks.tasks, [])


class FakeVoiceProvider:
    def __init__(self, provider_id, voices=(), error=None):
        self.provider_id, self.voices, self.error, self.calls = provider_id, voices, error, 0

    async def list_voices(self):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.voices)


class FakeVoiceRegistry:
    def __init__(self, providers):
        self.providers = providers

    def create(self, provider_id):
        return self.providers[provider_id]


class DubbingVoiceApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_dubbing_corrects_stale_voice_before_background_work(self):
        registry = FakeVoiceRegistry({
            "qwen": FakeVoiceProvider("qwen", [
                VoiceInfo("zh_female_1", provider="qwen", language="zh"),
            ]),
        })
        task = {
            "task_id": "task-1", "language": "English",
            "upload_path": "fixture.mp4", "transcription_path": "fixture.json",
        }
        background_tasks = BackgroundTasks()
        with patch("app.api.dubbing.history_manager.get_task", return_value=task), patch(
            "app.api.dubbing.Path.exists", return_value=True
        ), patch("app.api.dubbing.settings.TTS_MODE", "qwen"), patch(
            "app.api.dubbing.dubbing_service.tts_registry", registry
        ):
            await start_dubbing(DubbingRequest(
                task_id="task-1", target_lang="Chinese", voice="zf_xiaobei"
            ), background_tasks)

        self.assertEqual(background_tasks.tasks[0].args[2], "zh_female_1")

    async def test_stale_kokoro_voice_falls_back_to_current_qwen_language(self):
        registry = FakeVoiceRegistry({
            "qwen": FakeVoiceProvider("qwen", [
                VoiceInfo("zh_female_1", provider="qwen", language="zh"),
                VoiceInfo("en_female_1", provider="qwen", language="en"),
            ]),
        })

        selected = await resolve_available_voice(
            registry,
            TTSRoute(provider_id="qwen", language="Chinese"),
            "zf_xiaobei",
            "Chinese",
        )

        self.assertEqual(selected, "zh_female_1")

    async def test_current_provider_voice_is_preserved(self):
        registry = FakeVoiceRegistry({
            "qwen": FakeVoiceProvider("qwen", [
                VoiceInfo("zh_female_1", provider="qwen", language="zh"),
                VoiceInfo("zh_male_1", provider="qwen", language="zh"),
            ]),
        })

        selected = await resolve_available_voice(
            registry,
            TTSRoute(provider_id="qwen", language="Chinese"),
            "zh_male_1",
            "Chinese",
        )

        self.assertEqual(selected, "zh_male_1")

    async def test_qwen_voice_response_is_local_only(self):
        registry = FakeVoiceRegistry({
            "qwen": FakeVoiceProvider("qwen", [VoiceInfo(
                "ko_female_1", provider="qwen", language="ko"
            )]),
            "edge": FakeVoiceProvider("edge", [VoiceInfo(
                "ko-online", provider="edge", language="ko"
            )]),
        })
        with patch("app.api.dubbing.dubbing_service.tts_registry", registry), patch(
            "app.api.dubbing.settings.TTS_MODE", "qwen"
        ):
            result = await list_voices()
        self.assertEqual(result["tts_mode"], "qwen")
        self.assertEqual([voice["provider"] for voice in result["voices"]], ["qwen"])
        self.assertEqual(result["online_languages"], [])
        self.assertEqual(registry.providers["edge"].calls, 0)

    async def test_cosyvoice_voice_response_and_missing_bundle_error(self):
        registry = FakeVoiceRegistry({
            "cosyvoice": FakeVoiceProvider("cosyvoice", [VoiceInfo(
                "zh_female_1", provider="cosyvoice", language="zh"
            )]),
            "edge": FakeVoiceProvider("edge", []),
        })
        with patch("app.api.dubbing.dubbing_service.tts_registry", registry), patch(
            "app.api.dubbing.settings.TTS_MODE", "cosyvoice"
        ):
            result = await list_voices()
        self.assertEqual(result["tts_mode"], "cosyvoice_edge")
        self.assertEqual(result["voices"][0]["provider"], "cosyvoice")

        registry.providers["cosyvoice"] = FakeVoiceProvider(
            "cosyvoice", error=OSError("bundle missing")
        )
        with patch("app.api.dubbing.dubbing_service.tts_registry", registry), patch(
            "app.api.dubbing.settings.TTS_MODE", "cosyvoice"
        ):
            result = await list_voices()
        self.assertEqual(result["voices"], [])
        self.assertIn("bundle missing", result["error"])

    async def test_kokoro_voice_response_uses_normalized_registry_models(self):
        registry = FakeVoiceRegistry({
            "kokoro": FakeVoiceProvider("kokoro", [VoiceInfo("af_test", provider="kokoro", language="en")]),
            "edge": FakeVoiceProvider("edge", [VoiceInfo("ko-test", provider="edge", language="ko")]),
        })
        with patch("app.api.dubbing.dubbing_service.tts_registry", registry), patch(
            "app.api.dubbing.settings.TTS_MODE", "kokoro"
        ):
            result = await list_voices()
        self.assertEqual([voice["provider"] for voice in result["voices"]], ["kokoro"])
        self.assertEqual(result["tts_mode"], "kokoro")
        self.assertEqual(result["supported_languages"], ["Chinese", "English", "Japanese"])
        self.assertEqual(registry.providers["edge"].calls, 0)

    async def test_edge_voice_failure_preserves_legacy_response_shape(self):
        registry = FakeVoiceRegistry({
            "edge": FakeVoiceProvider("edge", error=OSError("offline")),
        })
        with patch("app.api.dubbing.dubbing_service.tts_registry", registry), patch(
            "app.api.dubbing.settings.TTS_MODE", "edge"
        ):
            result = await list_voices()
        self.assertEqual(result["voices"], [])
        self.assertEqual(result["supported_languages"], ["Chinese", "English", "Japanese", "Korean"])
        self.assertEqual(result["unsupported_languages"], ["zh", "en", "ja", "ko"])
        self.assertEqual(result["online_languages"], ["zh", "en", "ja", "ko"])
        self.assertIn("offline", result["edge_error"])


if __name__ == "__main__":
    unittest.main()
