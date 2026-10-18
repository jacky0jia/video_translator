import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.api.translation import translate_transcription
from app.core.schemas import TranscriptionResult, TranscriptionSegment


class History:
    def __init__(self, task): self.task, self.updates = task, []
    def get_task(self, task_id): return self.task
    def update_task(self, task_id, updates):
        self.updates.append(dict(updates))
        self.task.update(updates)
    def compare_and_update_task(self, task_id, expected_revision, updates):
        self.task.update(updates)
        self.task["state_revision"] = expected_revision + 1
        return dict(self.task)


def result(text="Source"):
    return TranscriptionResult(
        video_source="fixture.mp4", language="en",
        segments=[TranscriptionSegment(start=0, end=1, text=text, confidence=1)],
    )


class TranslationApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_translation_progress_is_persisted_for_reconnects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(result().model_dump_json(), encoding="utf-8")
            history = History({"task_id": "one", "transcription_path": str(source), "translations": {}})

            async def translate(**kwargs):
                await kwargs["progress_callback"](2, 4)
                return result("Translated")

            with patch("app.api.translation.history_manager", history), patch(
                "app.api.translation.translation_service.translate_full_result", side_effect=translate
            ), patch("app.api.translation.settings.OUTPUT_DIR", root), patch(
                "app.api.translation.emit_event"
            ):
                await translate_transcription(str(source), "Chinese", "one", pipeline=True)

        self.assertTrue(any(update.get("status") == "translating" and update.get("progress_percent") == 50
                            for update in history.updates))
        self.assertEqual(history.task["status"], "awaiting_review")

    async def test_pipeline_translation_fits_text_for_dubbing_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(result().model_dump_json(), encoding="utf-8")
            history = History({"task_id": "one", "transcription_path": str(source), "translations": {}})
            seen = []

            async def translate(**kwargs):
                seen.append(kwargs["fit_to_duration"])
                return result("Faithful translation")

            with patch("app.api.translation.history_manager", history), patch(
                "app.api.translation.translation_service.translate_full_result", side_effect=translate
            ), patch("app.api.translation.settings.OUTPUT_DIR", root), patch(
                "app.api.translation.emit_event"
            ):
                await translate_transcription(str(source), "Chinese", "one", pipeline=True)

        self.assertEqual(seen, [True])
        self.assertEqual(history.task["status"], "awaiting_review")
        self.assertEqual(history.task["translation_profiles"], {"Chinese": "dubbing"})

    async def test_subtitle_translation_records_faithful_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(result().model_dump_json(), encoding="utf-8")
            history = History({"task_id": "one", "transcription_path": str(source), "translations": {}})

            async def translate(**kwargs):
                self.assertFalse(kwargs["fit_to_duration"])
                return result("Complete subtitle translation")

            with patch("app.api.translation.history_manager", history), patch(
                "app.api.translation.translation_service.translate_full_result", side_effect=translate
            ), patch("app.api.translation.settings.OUTPUT_DIR", root), patch(
                "app.api.translation.emit_event"
            ):
                await translate_transcription(str(source), "Chinese", "one", pipeline=False)

        self.assertEqual(history.task["translation_profiles"], {"Chinese": "subtitles"})

    async def test_empty_translation_is_rejected_without_replacing_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            old = root / "old.json"
            source.write_text(result().model_dump_json(), encoding="utf-8")
            old.write_text(result("Old valid translation").model_dump_json(), encoding="utf-8")
            history = History({
                "task_id": "one", "transcription_path": str(source),
                "translation_path": str(old), "translations": {"Japanese": str(old)},
            })

            async def empty(**_kwargs): return result("   ")

            with patch("app.api.translation.history_manager", history), patch(
                "app.api.translation.translation_service.translate_full_result", side_effect=empty
            ), patch("app.api.translation.settings.OUTPUT_DIR", root), patch(
                "app.api.translation.emit_event"
            ):
                with self.assertRaises(HTTPException):
                    await translate_transcription(str(source), "Japanese", "one", pipeline=True)

        self.assertEqual(history.task["translation_path"], str(old))
        self.assertEqual(history.task["translations"]["Japanese"], str(old))
        self.assertEqual(history.task["status"], "translation_failed")

    async def test_partial_failure_does_not_replace_previous_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            old = root / "old.json"
            source.write_text(result().model_dump_json(), encoding="utf-8")
            old.write_text(result("Old valid translation").model_dump_json(), encoding="utf-8")
            history = History({
                "task_id": "one", "transcription_path": str(source),
                "translation_path": str(old), "translations": {"Chinese": str(old)},
            })

            async def failed(**_kwargs): return result("[Translation failed]")

            with patch("app.api.translation.history_manager", history), patch(
                "app.api.translation.translation_service.translate_full_result", side_effect=failed
            ), patch("app.api.translation.settings.OUTPUT_DIR", root), patch(
                "app.api.translation.emit_event"
            ):
                with self.assertRaises(HTTPException):
                    await translate_transcription(str(source), "Chinese", "one")

        self.assertEqual(history.task["translation_path"], str(old))
        self.assertEqual(history.task["translations"]["Chinese"], str(old))
        self.assertEqual(history.task["status"], "translation_failed")


if __name__ == "__main__": unittest.main()
