import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.pipeline import CompressionDecision, decide_compression
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.dubbing_service import DubbingCompressionRequired, DubbingService
from app.services.pipeline_service import PipelineService
from app.services.translation_service import TranslationService


class _History:
    def __init__(self, task): self.task = task
    def get_task(self, _task_id): return self.task
    def update_task(self, _task_id, changes): self.task.update(changes)


class DubbingApprovalTests(unittest.IsolatedAsyncioTestCase):
    def test_speed_caps_are_total_speech_speed(self):
        self.assertEqual(DubbingService._post_tts_tempo_cap("qwen", 1.0), 1.6)
        self.assertEqual(DubbingService._post_tts_tempo_cap("kokoro", 1.0), 1.4)
        self.assertEqual(DubbingService._post_tts_tempo_cap("edge", 1.2), 1.4 / 1.2)
        with self.assertRaises(ValueError):
            DubbingService._post_tts_tempo_cap("edge", 1.5)

    async def test_pipeline_waits_for_approval_without_overwriting_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"; source.touch()
            translated = Path(directory) / "translated.json"; translated.touch()
            history = _History({"task_id": "one", "transcription_path": str(source),
                                "translations": {"Chinese": str(translated)},
                                "translation_path": str(translated)})

            async def dub(*_args):
                raise DubbingCompressionRequired("needs shorter text", [0])

            async def export(*_args):
                return None

            service = PipelineService(dub_stage=dub, export_stage=export)
            with patch("app.services.pipeline_service.history_manager", history), patch(
                "app.services.pipeline_service.emit_event"
            ):
                await service.run("one", "Chinese", "voice")
            self.assertEqual(history.task["pipeline_status"], "awaiting_compression")
            self.assertEqual(history.task["translations"]["Chinese"], str(translated))
            self.assertEqual(history.task["compression_request"]["segment_indices"], [0])

    async def test_decline_keeps_original_translation(self):
        history = _History({"task_id": "one", "pipeline_status": "awaiting_compression",
                            "compression_request": {"segment_indices": [0]},
                            "translation_path": "original.json"})
        with patch("app.api.pipeline.history_manager", history), patch("app.api.pipeline.emit_event"):
            result = await decide_compression("one", CompressionDecision(approve=False))
        self.assertEqual(result["status"], "declined")
        self.assertEqual(history.task["translation_path"], "original.json")
        self.assertIsNone(history.task["compression_request"])

    async def test_approval_resumes_only_the_requested_task(self):
        request = {"segment_indices": [0], "target_lang": "Chinese", "voice": "voice", "speed": 1.0,
                   "burn_subtitles": False, "show_source": False, "show_target": True,
                   "subtitle_style": {}, "subtitle_format": "srt"}
        history = _History({"task_id": "one", "pipeline_status": "awaiting_compression",
                            "compression_request": request})
        with patch("app.api.pipeline.history_manager", history), patch(
            "app.api.pipeline.emit_event"
        ), patch("app.api.pipeline.pipeline_service.start") as start:
            result = await decide_compression("one", CompressionDecision(approve=True))
        self.assertEqual(result["status"], "started")
        self.assertTrue(start.call_args.kwargs["compress_translation"])
        self.assertTrue(start.call_args.kwargs["force_dub"])

    async def test_compression_changes_only_approved_row_and_keeps_original_model(self):
        service = TranslationService()
        source = TranscriptionResult(video_source="video", language="English", segments=[
            TranscriptionSegment(start=0, end=1, text="First", confidence=1),
            TranscriptionSegment(start=1, end=2, text="Second", confidence=1),
        ])
        translated = TranscriptionResult(video_source="video", language="Chinese", segments=[
            TranscriptionSegment(start=0, end=1, text="较长的第一句", confidence=1),
            TranscriptionSegment(start=1, end=2, text="第二句", confidence=1),
        ])
        service.translate_batch = AsyncMock(return_value=["第一句"])
        with patch.object(service, "_current_config", return_value=object()):
            compressed, changed = await service.compress_for_dubbing(source, translated, [0], "Chinese", "one")
        self.assertEqual(changed, [0])
        self.assertEqual(translated.segments[0].text, "较长的第一句")
        self.assertEqual(compressed.segments[0].text, "第一句")
        self.assertEqual(compressed.segments[1].text, "第二句")

    def test_shortening_cannot_discard_numbers_negation_or_causality(self):
        safe = TranslationService._safe_shorter_translation
        self.assertFalse(safe("因为失败了 2 次，所以不再试", "失败了，所以再试"))
        self.assertFalse(safe("如果延期，就停止", "延期就停止"))
        self.assertTrue(safe("这是一句比较冗长的译文", "这是较短译文"))

    async def test_approved_rewrite_becomes_the_shared_subtitle_and_voice_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, translation_path = root / "source.json", root / "translation.json"
            source = TranscriptionResult(video_source="video", language="English", segments=[
                TranscriptionSegment(start=0, end=2, text="First", confidence=1),
            ])
            original = TranscriptionResult(video_source="video", language="Chinese", segments=[
                TranscriptionSegment(start=0, end=2, text="较长的第一句", confidence=1),
            ])
            source_path.write_text(source.model_dump_json(), encoding="utf-8")
            translation_path.write_text(original.model_dump_json(), encoding="utf-8")
            history = _History({"task_id": "one", "transcription_path": str(source_path),
                                "translation_path": str(translation_path),
                                "translations": {"Chinese": str(translation_path)},
                                "compression_request": {"segment_indices": [0]}})
            compressed = original.model_copy(update={"segments": [
                original.segments[0].model_copy(update={"text": "第一句"}),
            ]})
            service = PipelineService()
            with patch("app.services.pipeline_service.history_manager", history), patch(
                "app.services.pipeline_service.settings.OUTPUT_DIR", root
            ), patch("app.services.translation_service.translation_service.compress_for_dubbing",
                     new_callable=AsyncMock, return_value=(compressed, [0])):
                await service._compress_translation("one", "Chinese")
            final_path = Path(history.task["translation_path"])
            self.assertEqual(history.task["translations"]["Chinese"], str(final_path))
            self.assertEqual(json.loads(final_path.read_text(encoding="utf-8"))["segments"][0]["text"], "第一句")
            self.assertEqual(json.loads(translation_path.read_text(encoding="utf-8"))["segments"][0]["text"], "较长的第一句")
