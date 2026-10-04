import unittest
from unittest.mock import AsyncMock, patch

from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.translation_service import TranslationService


class TranslationAlignmentSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_unresolved_row_is_saved_for_review_without_stopping(self):
        service = TranslationService()
        source = TranscriptionResult(
            video_source="fixture.mp4", language="English",
            segments=[
                TranscriptionSegment(start=0, end=2, text="First sentence", confidence=1),
                TranscriptionSegment(start=2, end=4, text="Second sentence", confidence=1),
            ],
        )
        service.translate_batch = AsyncMock(side_effect=[
            ["第一句和第二句", "第二句"], ["仍然错位"],
        ])
        with patch.object(service, "_audit_batch_alignment", new_callable=AsyncMock, side_effect=[[0], [0]]):
            result = await service.translate_full_result(source, batch_size=2)
        self.assertEqual(result.alignment_review_rows, [1])
        self.assertEqual(len(result.segments), 2)

    async def test_audit_failure_keeps_translation_and_marks_check_incomplete(self):
        service = TranslationService()
        source = TranscriptionResult(video_source="fixture.mp4", language="English", segments=[
            TranscriptionSegment(start=0, end=1, text="Hello", confidence=1),
        ])
        service.translate_batch = AsyncMock(return_value=["你好"])
        with patch.object(service, "_audit_batch_alignment", new_callable=AsyncMock, side_effect=RuntimeError("offline")):
            result = await service.translate_full_result(source, batch_size=1)
        self.assertEqual(result.segments[0].text, "你好")
        self.assertTrue(result.alignment_check_incomplete)
