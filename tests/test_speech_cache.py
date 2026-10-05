import io
import json
import math
import struct
import subprocess
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.config import settings
from app.core.provider_lifecycle import NoopProviderLifecycle
from app.services.dubbing_service import DubbingCancelled, DubbingService
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.pipeline_service import PipelineService
from app.services.tts.speech_cache import SpeechCache, synthesis_context, synthesis_signature


def audio_bytes(frames=2205, sample_rate=22050):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x01\x00" * frames)
    return stream.getvalue()


class SpeechCacheTests(unittest.TestCase):
    def test_missing_corrupt_truncated_or_wrong_format_audio_is_a_cache_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = SpeechCache(root, {"sample_rate": 22050}, speed=1, language="Chinese")
            audio = audio_bytes()
            self.assertIsNone(cache.get("text", "voice"))
            cache.put("text", "voice", audio)
            self.assertEqual(cache.get("text", "voice"), audio)
            wav_path = next(root.glob("*.wav"))
            wav_path.write_bytes(audio[:-2])
            self.assertIsNone(cache.get("text", "voice"))
            cache.put("text", "voice", audio[:-2])
            self.assertIsNone(cache.get("text", "voice"))
            cache.put("text", "voice", audio)
            wav_path.unlink()
            self.assertIsNone(cache.get("text", "voice"))
            cache.put("text", "voice", audio)
            next(root.glob("*.json")).write_text("[]", encoding="utf-8")
            self.assertIsNone(cache.get("text", "voice"))
            cache.put("other", "voice", audio_bytes(sample_rate=24000))
            self.assertIsNone(cache.get("other", "voice"))

    def test_every_synthesis_input_invalidates_and_metadata_contains_no_script_or_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"sample_rate": 22050, "provider": "speaches", "model": "one", "credential": "secret"}
            cache = SpeechCache(root, context, speed=1, language="Chinese")
            cache.put("private script", "voice", audio_bytes())
            self.assertIsNone(cache.get("edited script", "voice"))
            self.assertIsNone(cache.get("private script", "new voice"))
            variants = [dict(context, provider="edge"), dict(context, model="two"), dict(context, endpoint="new endpoint"),
                        dict(context, sample_rate=24000), dict(context, credential="new secret")]
            for variant in variants:
                self.assertIsNone(SpeechCache(root, variant, speed=1, language="Chinese").get("private script", "voice"))
            self.assertIsNone(SpeechCache(root, context, speed=1.1, language="Chinese").get("private script", "voice"))
            self.assertIsNone(SpeechCache(root, context, speed=1, language="English").get("private script", "voice"))
            manifest = next(root.glob("*.json")).read_text(encoding="utf-8")
            self.assertNotIn("secret", manifest)
            self.assertNotIn("private script", manifest)

    def test_local_model_and_voice_changes_invalidate_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, "BASE_DIR", Path(directory) / "app"):
            root = Path(directory) / "models" / "qwen3-tts"
            root.mkdir(parents=True)
            model = root / "model.gguf"
            model.write_bytes(b"model")
            (root / "bundle.json").write_text(json.dumps({"model_path": "model.gguf"}), encoding="utf-8")
            before = synthesis_context("qwen")
            model.write_bytes(b"different model")
            self.assertNotEqual(before, synthesis_context("qwen"))
            before = synthesis_context("qwen")
            voices = Path(directory) / "models" / "voices" / "librivox_public_domain"
            voices.mkdir(parents=True)
            (voices / "reference.wav").write_bytes(audio_bytes())
            self.assertNotEqual(before, synthesis_context("qwen"))

    def test_unwritable_cache_does_not_discard_new_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SpeechCache(Path(directory), {"sample_rate": 22050}, speed=1, language="Chinese")
            with patch.object(cache, "_atomic_write", side_effect=OSError("full disk")):
                cache.put("text", "voice", audio_bytes())
            self.assertIsNone(cache.get("text", "voice"))

    def test_completed_track_is_invalidated_when_synthesis_settings_change(self):
        with patch.object(settings, "TTS_MODE", "speaches"):
            task = {"dubbing_status": "completed", "dubbing_target_lang": "Chinese",
                    "dubbing_voice": "voice", "dubbing_speed": 1.0,
                    "dubbing_provider_signature": synthesis_signature("speaches")}
            with patch.object(PipelineService, "_artifact_exists", return_value=True):
                self.assertTrue(PipelineService._can_reuse_dubbing(task, "Chinese", "voice", 1))
                with patch.object(settings, "TTS_MODEL", "other model"):
                    self.assertFalse(PipelineService._can_reuse_dubbing(task, "Chinese", "voice", 1))


class SelectiveSynthesisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = object.__new__(DubbingService)
        self.service._cancel_events = {}
        self.adapter = SimpleNamespace(close=AsyncMock())
        self.service.tts_registry = SimpleNamespace(create=lambda _provider: self.adapter)
        self.lifecycle = SimpleNamespace(startup=AsyncMock(), shutdown=AsyncMock())
        self.service._lifecycle = self.lifecycle
        self.service._gpu_lifecycle = self.lifecycle
        self.service._synthesize_one = AsyncMock(return_value=audio_bytes())
        self.service._fetch_voice_fallbacks = AsyncMock(return_value=[])

    async def run_speech(self, root, segments, task="one", voice="voice", speed=1, language="Chinese", provider="edge"):
        work = root / "work"
        work.mkdir(exist_ok=True)
        with patch.object(settings, "TEMP_DIR", root), patch("app.services.dubbing_service.emit_event"), patch(
            "app.services.dubbing_service.history_manager.update_task"
        ):
            return await self.service._synthesize_all(segments, voice, speed, task, work, provider, language)

    async def test_only_changed_utterance_is_synthesized_and_reordered_or_retimed_speech_reuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = [SimpleNamespace(text=text) for text in ("First.", "Second.", "Third.")]
            paths = await self.run_speech(root, original)
            unchanged_audio = paths[0].read_bytes()
            self.assertEqual(self.service._synthesize_one.await_count, 3)
            self.service._synthesize_one.reset_mock()
            self.service._synthesize_one.return_value = audio_bytes(frames=4410)
            changed = [original[0], SimpleNamespace(text="Shorter."), original[2]]
            paths = await self.run_speech(root, changed)
            self.service._synthesize_one.assert_awaited_once()
            self.assertEqual(self.service._synthesize_one.await_args.args[1], "Shorter.")
            self.assertEqual(paths[0].read_bytes(), unchanged_audio)
            self.assertEqual(paths[2].read_bytes(), unchanged_audio)
            self.assertNotEqual(paths[1].read_bytes(), unchanged_audio)
            self.service._synthesize_one.reset_mock()
            self.lifecycle.startup.reset_mock()
            self.lifecycle.shutdown.reset_mock()
            retimed = [SimpleNamespace(text=s.text, start=10, end=12) for s in reversed(changed)]
            await self.run_speech(root, retimed)
            self.service._synthesize_one.assert_not_awaited()
            self.lifecycle.startup.assert_not_awaited()
            self.lifecycle.shutdown.assert_not_awaited()

    async def test_cache_is_task_scoped_and_corrupt_clip_alone_is_regenerated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [SimpleNamespace(text="First."), SimpleNamespace(text="Second.")]
            await self.run_speech(root, rows)
            self.service._synthesize_one.reset_mock()
            next((root / "one" / "speech-cache").glob("*.wav")).write_bytes(b"broken")
            await self.run_speech(root, rows)
            self.service._synthesize_one.assert_awaited_once()
            self.service._synthesize_one.reset_mock()
            await self.run_speech(root, rows, task="other")
            self.assertEqual(self.service._synthesize_one.await_count, 2)

    async def test_auto_downloaded_assets_use_final_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [SimpleNamespace(text="First.")]
            context = lambda _provider: {"sample_rate": 22050, "assets_present": self.service._synthesize_one.await_count > 0}
            with patch("app.services.dubbing_service.synthesis_context", side_effect=context):
                await self.run_speech(root, rows)
                await self.run_speech(root, rows)
            self.service._synthesize_one.assert_awaited_once()

    async def test_cancellation_is_respected_even_when_all_audio_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [SimpleNamespace(text="First.")]
            await self.run_speech(root, rows)
            self.service._synthesize_one.reset_mock()
            cancellation = threading.Event()
            cancellation.set()
            self.service._cancel_events["one"] = cancellation
            with self.assertRaises(DubbingCancelled):
                await self.run_speech(root, rows)
            self.service._synthesize_one.assert_not_awaited()

    async def test_fallback_audio_cannot_be_reused_as_the_primary_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.service._fetch_voice_fallbacks.return_value = ["fallback"]
            self.service._synthesize_one.side_effect = [RuntimeError("primary failed"), audio_bytes()]
            await self.run_speech(root, [SimpleNamespace(text="First.")], provider="kokoro")
            self.service._synthesize_one.reset_mock()
            self.service._synthesize_one.side_effect = None
            await self.run_speech(root, [SimpleNamespace(text="First.")], provider="kokoro")
            self.service._synthesize_one.assert_awaited_once()
            self.assertEqual(self.service._synthesize_one.await_args.args[2], "voice")

    async def test_compression_retry_reuses_speech_after_cleanup_and_rebuilds_real_track_and_video(self):
        self.service.ffmpeg_path = settings.ffmpeg_path
        if not Path(self.service.ffmpeg_path).is_file():
            self.skipTest("FFmpeg is not installed")

        def voiced_audio(seconds):
            stream = io.BytesIO()
            with wave.open(stream, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(22050)
                wav.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 22050)))
                                         for i in range(int(seconds * 22050))))
            return stream.getvalue()

        long_audio, short_audio = voiced_audio(6), voiced_audio(0.5)
        self.service._synthesize_one.side_effect = lambda _client, text, *_args, **_kwargs: (
            long_audio if text == "这是原来很长的第一句。" else short_audio
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "source.mp4"
            result = subprocess.run([self.service.ffmpeg_path, "-y", "-f", "lavfi", "-i",
                                     "color=c=black:s=160x90:r=25:d=4", "-c:v", "libx264", "-an", str(video)],
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            source = TranscriptionResult(video_source=str(video), language="English", segments=[
                TranscriptionSegment(start=0, end=1, text="First.", confidence=1),
                TranscriptionSegment(start=2, end=3, text="Second.", confidence=1),
            ])
            translation = source.model_copy(update={"language": "Chinese", "segments": [
                source.segments[0].model_copy(update={"text": "这是原来很长的第一句。"}),
                source.segments[1].model_copy(update={"text": "第二句。"}),
            ]})
            source_path, translation_path = root / "source.json", root / "translation.json"
            source_path.write_text(source.model_dump_json(), encoding="utf-8")
            translation_path.write_text(translation.model_dump_json(), encoding="utf-8")
            task = {"task_id": "one", "filename": video.name, "upload_path": str(video),
                    "transcription_path": str(source_path), "translation_path": str(translation_path),
                    "translations": {"Chinese": str(translation_path)}}
            history = SimpleNamespace(get_task=lambda _task: task,
                                      update_task=lambda _task, data: task.update(data))
            pipeline = PipelineService(translate_stage=AsyncMock())
            timeline = self.service._build_timeline
            with patch.object(settings, "TEMP_DIR", root / "temp"), patch.object(settings, "OUTPUT_DIR", root / "output"), patch.object(
                settings, "TTS_MODE", "edge"
            ), patch("app.services.pipeline_service.history_manager", history), patch(
                "app.services.dubbing_service.history_manager", history
            ), patch("app.services.dubbing_service.dubbing_service", self.service), patch(
                "app.services.pipeline_service.emit_event"
            ), patch("app.services.dubbing_service.emit_event"), patch(
                "app.services.translation_service.translation_service._lifecycle", NoopProviderLifecycle()
            ), patch(
                "app.services.translation_service.translation_service.translate_batch",
                new_callable=AsyncMock, side_effect=[["短句。"], ["第二句。"]]
            ), patch.object(self.service, "_build_timeline", wraps=timeline) as build:
                await pipeline.run("one", "Chinese", "voice")
                self.assertEqual(task["pipeline_status"], "awaiting_compression")
                self.assertFalse((root / "temp" / "dub_one").exists())
                self.assertTrue((root / "temp" / "one" / "speech-cache").is_dir())
                self.assertEqual(self.service._synthesize_one.await_count, 2)
                self.service._synthesize_one.reset_mock()
                await pipeline.run("one", "Chinese", "voice", force_dub=True, compress_translation=True)
                self.assertEqual(task["pipeline_status"], "completed", task.get("message"))
                self.service._synthesize_one.assert_awaited_once()
                self.assertEqual(self.service._synthesize_one.await_args.args[1], "短句。")
                self.assertEqual(task["dubbing_synthesis"], {"reused": 1, "synthesized": 1, "utterances": 2})
                self.assertEqual(build.call_count, 2)
                pipeline._translate_stage.assert_not_awaited()
                subtitle_path = Path(task["subtitle_outputs"]["srt"])
                self.assertIn("短句。", subtitle_path.read_text(encoding="utf-8"))
                final = json.loads(Path(task["translation_path"]).read_text(encoding="utf-8"))
                self.assertEqual([s["text"] for s in final["segments"]], ["短句。", "第二句。"])
                self.assertEqual(json.loads(translation_path.read_text(encoding="utf-8"))["segments"][0]["text"], "这是原来很长的第一句。")
                output_video = root / "output" / Path(task["dubbing_video_path"]).name
                self.assertTrue(output_video.is_file())
                self.assertAlmostEqual(self.service._probe_duration(output_video), 4.0, delta=0.1)
