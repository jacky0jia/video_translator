import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.api.tasks import SegmentEdit, edit_segment


class _History:
    def __init__(self, task):
        self.task = task

    def get_task(self, _task_id):
        return self.task

    def update_task(self, _task_id, changes):
        self.task.update(changes)


class SubtitleTimingSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_time_edit_updates_source_and_translation_and_stales_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, translated = root / "source.json", root / "translated.json"
            for path in (source, translated):
                path.write_text(json.dumps({"segments": [{"start": 0, "end": 3, "text": "line"}]}), encoding="utf-8")
            history = _History({
                "task_id": "task", "transcription_path": str(source),
                "translation_path": str(translated), "translations": {"Chinese": str(translated)},
                "dubbing_audio_path": "old.wav", "dubbing_status": "completed", "status": "completed",
            })
            with patch("app.api.tasks.history_manager", history), patch("app.api.tasks._is_safe_path", return_value=True):
                result = await edit_segment("task", 0, SegmentEdit(start=1.25, end=2.5))
            self.assertEqual(result["updated"], ["timing"])
            for path in (source, translated):
                row = json.loads(path.read_text(encoding="utf-8"))["segments"][0]
                self.assertEqual((row["start"], row["end"]), (1.25, 2.5))
            self.assertEqual(history.task["dubbing_status"], "stale")
            self.assertEqual(history.task["subtitle_outputs"], {})

    async def test_invalid_time_is_rejected_before_writing(self):
        history = _History({"task_id": "task"})
        with patch("app.api.tasks.history_manager", history):
            with self.assertRaises(HTTPException) as error:
                await edit_segment("task", 0, SegmentEdit(start=3, end=2))
        self.assertEqual(error.exception.status_code, 400)
