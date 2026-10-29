import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.asr_quality import transcribe_checked
from app.services.translation_service import TranslationService


def raw(text="Normal speech.", score=-0.2):
    return SimpleNamespace(start=104, end=112, text=text, avg_logprob=score)


class ASRQualityTests(unittest.TestCase):
    def test_legacy_null_and_nonfinite_confidence_round_trip(self):
        for value in (None, float("nan"), float("inf"), float("-inf"), -0.25):
            with self.subTest(value=value):
                row = TranscriptionSegment(start=104, end=112, text="", confidence=value)
                self.assertTrue(math.isfinite(row.confidence))
                saved = row.model_dump_json()
                self.assertNotIn("null", saved)
                self.assertEqual(TranscriptionSegment.model_validate_json(saved), row)
        with self.assertRaises(ValidationError):
            TranscriptionSegment(start=0, end=1, text="", confidence="invalid")

    def test_normal_decode_does_not_retry(self):
        info = SimpleNamespace(language="en")
        model = Mock()
        model.transcribe.return_value = (iter([raw("Wait... (really?)")]), info)
        segments, actual_info = transcribe_checked(model, "audio.wav", chunk_length=8)
        self.assertEqual(len(segments), 1)
        self.assertIs(actual_info, info)
        model.transcribe.assert_called_once_with("audio.wav", language=None, chunk_length=8)

    def test_repeat_and_invalid_scores_retry_once_without_dropping_speech(self):
        for first in (raw(")" * 200), raw(score=float("nan")), raw(score=float("-inf")),
                      raw(") ] " * 40)):
            with self.subTest(first=first):
                model = Mock()
                info = SimpleNamespace(language="en")
                model.transcribe.side_effect = [(iter([first]), info), (iter([raw("Recovered speech")]), info)]
                segments, _ = transcribe_checked(
                    model, "audio.wav", beam_size=5, chunk_length=30,
                    word_timestamps=True, initial_prompt="Keep caller context.",
                )
                self.assertEqual(segments[0].text, "Recovered speech")
                options = model.transcribe.call_args.kwargs
                self.assertEqual(options["language"], "en")
                self.assertEqual(options["chunk_length"], 30)
                self.assertEqual(options["temperature"], 0)
                self.assertFalse(options["condition_on_previous_text"])
                self.assertEqual(options["beam_size"], 5)
                self.assertTrue(options["word_timestamps"])
                self.assertEqual(options["initial_prompt"], "Keep caller context.")
                self.assertNotIn("vad_filter", options)
                self.assertNotIn("hallucination_silence_threshold", options)

    def test_persistent_failure_or_empty_retry_is_explicit(self):
        for retry in ([raw(")" * 200)], []):
            model = Mock()
            info = SimpleNamespace(language="en")
            model.transcribe.side_effect = [(iter([raw(")" * 200)]), info), (iter(retry), info)]
            with self.assertRaisesRegex(RuntimeError, "No corrupted transcription was saved"):
                transcribe_checked(model, "audio.wav", language="en")
            self.assertEqual(model.transcribe.call_count, 2)

    def test_worker_uses_quality_check_and_serializes_split_subtitle_rows(self):
        from app.workers.asr_worker import _worker_main
        connection = Mock()
        connection.recv.side_effect = [
            {"type": "transcribe", "audio_path": "audio.wav", "language": "en"},
            {"type": "shutdown"},
        ]
        model = Mock()
        fake = SimpleNamespace(WhisperModel=Mock(return_value=model))
        segment = SimpleNamespace(
            start=0.0, end=12.0, text=" First second. Third fourth. ", avg_logprob=-0.25,
            words=[
                SimpleNamespace(start=0.0, end=3.0, word=" First"),
                SimpleNamespace(start=3.0, end=6.0, word=" second."),
                SimpleNamespace(start=6.0, end=9.0, word=" Third"),
                SimpleNamespace(start=9.0, end=12.0, word=" fourth."),
            ],
        )
        checked = Mock(return_value=([segment], SimpleNamespace(language="en", language_probability=1)))
        with patch.dict("sys.modules", {"faster_whisper": fake}), patch(
            "app.workers.asr_worker.transcribe_checked", checked
        ):
            _worker_main(
                connection, {"device": "cpu"},
                {"beam_size": 5, "task": "transcribe", "chunk_length": 30, "word_timestamps": True},
                8,
            )
        checked.assert_called_once_with(
            model, "audio.wav", language="en", beam_size=5, task="transcribe",
            chunk_length=30, word_timestamps=True,
        )
        self.assertEqual(connection.send.call_args_list[1].args[0], {
            "ok": True, "type": "result", "language": "en", "language_probability": 1,
            "segments": [
                {"start": 0.0, "end": 6.0, "text": "First second.", "confidence": -0.25,
                 "source_text_edited": False},
                {"start": 6.0, "end": 12.0, "text": "Third fourth.", "confidence": -0.25,
                 "source_text_edited": False},
            ],
        })
        connection.close.assert_called_once()
        model.model.unload_model.assert_called_once()


class ASRWorkerStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_start_passes_subtitle_duration_separately_to_process(self):
        from app.workers.asr_worker import ASRWorkerClient

        context = Mock()
        parent, child = Mock(), Mock()
        context.Pipe.return_value = (parent, child)
        worker = ASRWorkerClient()
        worker._receive = AsyncMock(return_value={"ok": True, "type": "ready"})
        model_args = {"device": "cpu"}
        transcribe_args = {"chunk_length": 30, "word_timestamps": True}
        with patch("app.workers.asr_worker.multiprocessing.get_context", return_value=context):
            await worker.start(
                model_args=model_args, transcribe_args=transcribe_args,
                subtitle_segment_seconds=4,
            )
        self.assertEqual(
            context.Process.call_args.kwargs["args"], (child, model_args, transcribe_args, 4)
        )
        context.Process.return_value.start.assert_called_once()
        child.close.assert_called_once()


class ClearedSubtitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleared_row_is_not_translated_and_review_numbers_are_restored(self):
        source = TranscriptionResult(video_source="a.wav", language="en", segments=[
            TranscriptionSegment(start=i, end=i+1, text=text, confidence=None)
            for i, text in enumerate(("", "First", " ", "Second"))
        ])
        service = TranslationService()
        service.translate_batch = AsyncMock(side_effect=[["第一句", "第二句"], ["第二句"]])
        service._audit_batch_alignment = AsyncMock(return_value=[1])
        translated = await service.translate_full_result(source, batch_size=2)
        self.assertEqual([s.text for s in translated.segments], ["", "第一句", "", "第二句"])
        self.assertEqual([(s.start, s.end) for s in translated.segments], [(i, i+1) for i in range(4)])
        self.assertEqual(translated.alignment_review_rows, [4])
        self.assertEqual([s.text for s in service.translate_batch.call_args_list[0].args[0]], ["First", "Second"])

    async def test_edit_then_translate_legacy_null_through_real_api(self):
        from app.api.tasks import SegmentEdit, edit_segment
        from app.api.translation import translate_transcription
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps({"video_source": "a.wav", "language": "en", "segments": [
                {"start": 104, "end": 112, "text": ")" * 200, "confidence": None},
                {"start": 112, "end": 114, "text": "Normal", "confidence": -0.2},
            ]}), encoding="utf-8")
            history = Mock()
            history.get_task.return_value = {"task_id": "task", "transcription_path": str(source)}
            history.update_task.side_effect = lambda _id, updates: history.get_task.return_value.update(updates)
            def compare_review(_id, revision, updates):
                history.get_task.return_value.update(updates, state_revision=revision + 1)
                return dict(history.get_task.return_value)
            history.compare_and_update_task.side_effect = compare_review
            service = TranslationService()
            service.translate_batch = AsyncMock(return_value=["正常"])
            service._audit_batch_alignment = AsyncMock(return_value=[])
            with patch("app.api.tasks.history_manager", history), patch(
                "app.api.translation.history_manager", history
            ), patch("app.api.translation.settings.OUTPUT_DIR", root), patch(
                "app.api.translation.translation_service", service
            ), patch("app.api.translation.emit_event"):
                await edit_segment("task", 0, SegmentEdit(source_text=""))
                result = await translate_transcription(str(source), "Chinese", "task")
            saved = TranscriptionResult.model_validate_json(Path(result["translated_file"]).read_text(encoding="utf-8"))
            self.assertEqual([s.text for s in saved.segments], ["", "正常"])
            self.assertTrue(math.isfinite(saved.segments[0].confidence))

    async def test_all_blank_source_does_not_call_a_provider(self):
        service = TranslationService()
        service.translate_batch = AsyncMock()
        source = TranscriptionResult(video_source="a.wav", language="en", segments=[
            TranscriptionSegment(start=0, end=1, text="", confidence=None),
        ])
        result = await service.translate_full_result(source, "Chinese")
        self.assertEqual(result.segments[0].text, "")
        service.translate_batch.assert_not_awaited()
