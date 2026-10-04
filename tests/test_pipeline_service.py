import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.services.pipeline_service import PipelineService


class History:
    def __init__(self, task): self.task = task
    def get_task(self, task_id): return self.task if self.task["task_id"] == task_id else None
    def update_task(self, task_id, updates): self.task.update(updates)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_state_machine_runs_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"; source.touch()
            history = History({
                "task_id": "one", "source_path": str(source), "filename": source.name,
                "failed_stage": "old", "cancelled_stage": "old",
            })
            order = []

            async def transcribe(task_id, task):
                order.append("asr")
                path = root / "transcription.json"; path.touch()
                history.task["transcription_path"] = str(path)

            async def translate(task_id, language):
                order.append("translation")
                path = root / "translation.json"; path.touch()
                history.task["translations"] = {language: str(path)}
                history.task["pipeline_translation_targets"] = [language]

            async def dub(task_id, language, voice, speed):
                order.append("tts")
                audio = root / "audio.wav"; audio.touch()
                video = root / "dubbed.mp4"; video.touch()
                history.task.update({
                    "dubbing_audio_path": str(audio),
                    "dubbing_raw_video_path": str(video),
                    "dubbing_video_path": str(video),
                })

            service = PipelineService(transcribe_stage=transcribe, translate_stage=translate, dub_stage=dub)
            with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
                await service.run("one", "Chinese", "zf_xiaoxiao")

        self.assertEqual(order, ["asr", "translation", "tts"])
        self.assertEqual(history.task["pipeline_status"], "completed")
        self.assertEqual(history.task["status"], "completed")
        self.assertIsNone(history.task["failed_stage"])
        self.assertIsNone(history.task["cancelled_stage"])

    async def test_default_export_persists_requested_subtitle(self):
        from app.core.schemas import TranscriptionResult, TranscriptionSegment

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translation = root / "translated.json"
            translation.write_text(
                TranscriptionResult(
                    video_source="fixture.mp4", language="Chinese",
                    segments=[TranscriptionSegment(start=0, end=1, text="译文", confidence=1)],
                ).model_dump_json(),
                encoding="utf-8",
            )
            history = History({
                "task_id": "one", "translation_path": str(translation),
                "translations": {"Chinese": str(translation)}, "subtitle_outputs": {},
            })
            service = PipelineService()
            with patch("app.services.pipeline_service.history_manager", history), patch(
                "app.services.formatter_service.settings.OUTPUT_DIR", root
            ):
                await service._default_export("one", "Chinese", "vtt")

            output = Path(history.task["subtitle_outputs"]["vtt"])
            self.assertTrue(output.exists())
            self.assertIn("WEBVTT", output.read_text(encoding="utf-8-sig"))

    async def test_optional_burn_uses_raw_dubbed_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcription = root / "asr.json"; transcription.touch()
            translation = root / "zh.json"; translation.touch()
            audio = root / "audio.wav"; audio.touch()
            raw_video = root / "dubbed.mp4"; raw_video.touch()
            burned_video = root / "dubbed_subtitled.mp4"; burned_video.touch()
            history = History({
                "task_id": "one", "transcription_path": str(transcription),
                "translations": {"Chinese": str(translation)},
                "translation_profiles": {"Chinese": "dubbing"},
                "pipeline_translation_targets": ["Chinese"],
                "dubbing_status": "completed", "dubbing_target_lang": "Chinese",
                "dubbing_voice": "voice", "dubbing_speed": 1.0,
                "dubbing_audio_path": str(audio),
                "dubbing_raw_video_path": str(raw_video),
                "dubbing_video_path": str(raw_video),
            })
            burn_calls = []

            async def burn(task_id, language, source, target, style, video_path, end_note_enabled):
                burn_calls.append((task_id, language, source, target, style, video_path, end_note_enabled))
                history.task["dubbing_video_path"] = str(burned_video)

            service = PipelineService(burn_stage=burn)
            style = {"targetColor": "#ffffff"}
            with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
                await service.run(
                    "one", "Chinese", "voice", 1.0,
                    burn_subtitles=True, show_source=True,
                    show_target=True, subtitle_style=style,
                )

        self.assertEqual(
            burn_calls,
            [("one", "Chinese", True, True, style, raw_video, False)],
        )
        self.assertEqual(history.task["dubbing_video_path"], str(burned_video))
        self.assertTrue(history.task["dubbing_burn_subtitles"])

    async def test_end_note_renders_without_subtitles_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            translation = root / "zh.json"; translation.touch()
            audio = root / "dub.wav"; audio.touch()
            raw_video = root / "raw.mp4"; raw_video.touch()
            final_video = root / "final.mp4"; final_video.touch()
            history = History({
                "task_id": "one", "transcription_path": str(translation),
                "translations": {"Chinese": str(translation)},
                "dubbing_status": "completed", "dubbing_target_lang": "Chinese",
                "dubbing_voice": "voice", "dubbing_speed": 1.0,
                "dubbing_audio_path": str(audio), "dubbing_raw_video_path": str(raw_video),
                "dubbing_video_path": str(raw_video),
            })
            calls = []

            async def burn(*args):
                calls.append(args)
                history.task["dubbing_video_path"] = str(final_video)

            service = PipelineService(burn_stage=burn)
            with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
                await service.run("one", "Chinese", "voice", end_note_enabled=True)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][2:4], (False, False))
                self.assertTrue(calls[0][-1])
                self.assertFalse(history.task["dubbing_burn_subtitles"])
                self.assertTrue(history.task["dubbing_end_note_enabled"])
                await service.run("one", "Chinese", "voice", end_note_enabled=False)
                self.assertEqual(len(calls), 1)
                self.assertEqual(history.task["dubbing_video_path"], str(raw_video))

    async def test_resume_skips_existing_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcription = root / "asr.json"; transcription.touch()
            translation = root / "zh.json"; translation.touch()
            audio = root / "audio.wav"; audio.touch()
            video = root / "video.mp4"; video.touch()
            history = History({
                "task_id": "one", "transcription_path": str(transcription),
                "translations": {"Chinese": str(translation)},
                "translation_profiles": {"Chinese": "dubbing"},
                "pipeline_translation_targets": ["Chinese"],
                "dubbing_status": "completed", "dubbing_target_lang": "Chinese",
                "dubbing_voice": "voice", "dubbing_speed": 1.0,
                "dubbing_audio_path": str(audio), "dubbing_video_path": str(video),
            })
            service = PipelineService(
                transcribe_stage=lambda *args: self.fail("ASR should be skipped"),
                translate_stage=lambda *args: self.fail("translation should be skipped"),
                dub_stage=lambda *args: self.fail("dubbing should be skipped"),
            )
            with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
                await service.run("one", "Chinese", "voice")
        self.assertEqual(history.task["pipeline_status"], "completed")

    async def test_edited_subtitle_translation_is_reused_for_dubbing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcription = root / "asr.json"; transcription.touch()
            old_translation = root / "old-ja.json"; old_translation.touch()
            history = History({
                "task_id": "one", "transcription_path": str(transcription),
                "translations": {"Japanese": str(old_translation)},
            })
            calls = []

            async def translate(_task_id, language):
                calls.append("translate")
                fitted = root / "fitted-ja.json"; fitted.touch()
                history.task["translations"] = {language: str(fitted)}
                history.task["translation_profiles"] = {language: "dubbing"}

            async def dub(*_args):
                calls.append("dub")
                video = root / "dubbed.mp4"; video.touch()
                history.task["dubbing_video_path"] = str(video)

            service = PipelineService(translate_stage=translate, dub_stage=dub)
            with patch("app.services.pipeline_service.history_manager", history), patch(
                "app.services.pipeline_service.emit_event"
            ):
                await service.run("one", "Japanese", "ja_female_1")

        self.assertEqual(calls, ["dub"])
        self.assertEqual(history.task["translations"]["Japanese"], str(old_translation))

    async def test_force_translation_regenerates_translation_and_dubbing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcription = root / "asr.json"; transcription.touch()
            translation = root / "zh.json"; translation.touch()
            old_audio = root / "old.wav"; old_audio.touch()
            old_video = root / "old.mp4"; old_video.touch()
            history = History({
                "task_id": "one", "transcription_path": str(transcription),
                "translation_path": str(translation),
                "translations": {"Chinese": str(translation)},
                "dubbing_status": "completed", "dubbing_target_lang": "Chinese",
                "dubbing_voice": "voice", "dubbing_speed": 1.0,
                "dubbing_audio_path": str(old_audio),
                "dubbing_raw_video_path": str(old_video),
                "dubbing_video_path": str(old_video),
            })
            calls = []

            async def translate(*_args): calls.append("translate")
            async def export(*_args):
                calls.append("export")
                subtitle = root / "fresh.srt"; subtitle.touch()
                history.task["subtitle_outputs"] = {"srt": str(subtitle)}
            async def dub(*_args):
                calls.append("dub")
                fresh = root / "fresh.mp4"; fresh.touch()
                history.task["dubbing_raw_video_path"] = str(fresh)
                history.task["dubbing_video_path"] = str(fresh)

            service = PipelineService(
                translate_stage=translate, export_stage=export, dub_stage=dub
            )
            with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
                await service.run("one", "Chinese", "voice", force_translate=True)

        self.assertEqual(calls, ["translate", "export", "dub"])

    async def test_target_language_change_regenerates_dubbing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcription = root / "asr.json"; transcription.touch()
            korean = root / "ko.json"; korean.touch()
            old_audio = root / "zh.wav"; old_audio.touch()
            old_video = root / "zh.mp4"; old_video.touch()
            history = History({
                "task_id": "one", "transcription_path": str(transcription),
                "translations": {"Korean": str(korean)},
                "translation_profiles": {"Korean": "dubbing"},
                "pipeline_translation_targets": ["Korean"],
                "dubbing_status": "completed", "dubbing_target_lang": "Chinese",
                "dubbing_voice": "zf_xiaobei", "dubbing_speed": 1.0,
                "dubbing_audio_path": str(old_audio),
                "dubbing_video_path": str(old_video),
            })
            calls = []

            async def dub(task_id, language, voice, speed):
                calls.append((language, voice, speed))
                history.task.update({
                    "dubbing_status": "completed",
                    "dubbing_target_lang": language,
                    "dubbing_voice": voice,
                    "dubbing_speed": speed,
                })

            service = PipelineService(
                transcribe_stage=lambda *args: self.fail("ASR should be skipped"),
                translate_stage=lambda *args: self.fail("translation should be skipped"),
                dub_stage=dub,
            )
            with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
                await service.run("one", "Korean", "ko-KR-SunHiNeural", 1.25)

        self.assertEqual(calls, [("Korean", "ko-KR-SunHiNeural", 1.25)])
        self.assertEqual(history.task["dubbing_target_lang"], "Korean")

    async def test_voice_or_speed_change_regenerates_dubbing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcription = root / "asr.json"; transcription.touch()
            translation = root / "ja.json"; translation.touch()
            audio = root / "ja.wav"; audio.touch()
            video = root / "ja.mp4"; video.touch()
            history = History({
                "task_id": "one", "transcription_path": str(transcription),
                "translations": {"Japanese": str(translation)},
                "translation_profiles": {"Japanese": "dubbing"},
                "pipeline_translation_targets": ["Japanese"],
                "dubbing_status": "completed", "dubbing_target_lang": "Japanese",
                "dubbing_voice": "jf_alpha", "dubbing_speed": 1.0,
                "dubbing_audio_path": str(audio), "dubbing_video_path": str(video),
            })
            calls = []

            async def dub(*args): calls.append(args)

            service = PipelineService(dub_stage=dub)
            with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
                await service.run("one", "Japanese", "jf_alpha", 1.2)

        self.assertEqual(len(calls), 1)

    async def test_failure_records_stage_and_stops_pipeline(self):
        history = History({"task_id": "one", "transcription_path": "missing.json"})
        called = []

        async def fail_asr(*args): raise RuntimeError("worker crashed")
        async def later(*args): called.append(True)
        service = PipelineService(transcribe_stage=fail_asr, translate_stage=later, dub_stage=later)
        with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
            await service.run("one", "Chinese", "voice")
        self.assertEqual(history.task["pipeline_status"], "failed")
        self.assertEqual(history.task["failed_stage"], "pipeline_transcribing")
        self.assertEqual(called, [])

    async def test_http_exception_detail_is_preserved_as_pipeline_message(self):
        history = History({"task_id": "one", "transcription_path": "missing.json"})

        async def fail_asr(*_args):
            raise HTTPException(status_code=500, detail="Translation failed: provider timeout")

        service = PipelineService(transcribe_stage=fail_asr)
        with patch("app.services.pipeline_service.history_manager", history), patch(
            "app.services.pipeline_service.emit_event"
        ):
            await service.run("one", "Chinese", "voice")

        self.assertEqual(history.task["message"], "Translation failed: provider timeout")

    async def test_cancel_runs_stage_cleanup_and_sets_terminal_state(self):
        history = History({"task_id": "one"})
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def blocking(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        service = PipelineService(transcribe_stage=blocking)
        with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
            service.start("one", "Chinese", "voice")
            await entered.wait()
            self.assertTrue(await service.cancel("one"))
        self.assertTrue(cleaned.is_set())
        self.assertEqual(history.task["status"], "cancelled")
        self.assertEqual(service._tasks, {})

    async def test_application_shutdown_cancels_all_active_pipelines(self):
        history = History({"task_id": "one"})
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def blocking(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        service = PipelineService(transcribe_stage=blocking)
        with patch("app.services.pipeline_service.history_manager", history), patch("app.services.pipeline_service.emit_event"):
            service.start("one", "Chinese", "voice")
            await entered.wait()
            await service.shutdown()
        self.assertTrue(cleaned.is_set())
        self.assertEqual(service._tasks, {})
        self.assertEqual(history.task["status"], "cancelled")


if __name__ == "__main__": unittest.main()
