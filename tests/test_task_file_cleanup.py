import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api.tasks import delete_task
from app.core.history import HistoryManager


class TaskFileCleanupTests(unittest.TestCase):
    def test_delete_removes_all_pipeline_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upload, output, temp = root / "uploads", root / "output", root / "temp"
            for folder in (upload, output, temp): folder.mkdir()
            files = {
                "upload_path": upload / "video.mp4",
                "transcription_path": output / "asr.json",
                "translation_path": output / "translation.json",
                "extra": output / "ja.json",
                "burn_path": output / "burn.mp4",
                "dubbing_audio_path": output / "dub.wav",
                "dubbing_video_path": output / "dub.mp4",
            }
            for path in files.values(): path.write_bytes(b"fixture")
            task_temp = temp / "task-1"; task_temp.mkdir(); (task_temp / "part.wav").write_bytes(b"fixture")
            dub_temp = temp / "dub_pending"; dub_temp.mkdir(); (dub_temp / "raw.wav").write_bytes(b"fixture")

            history = HistoryManager(root / "history.json")
            task_id = history.create_task("video.mp4", "upload", str(files["upload_path"]))
            history.update_task(task_id, {
                "status": "completed",
                "transcription_path": str(files["transcription_path"]),
                "translation_path": str(files["translation_path"]),
                "translations": {"Japanese": str(files["extra"])},
                "burn_path": "/static/output/burn.mp4",
                "dubbing_audio_path": "/static/output/dub.wav",
                "dubbing_video_path": "/static/output/dub.mp4",
            })
            # The production temp convention uses the task id directly.
            task_temp.rename(temp / task_id)
            dub_temp.rename(temp / f"dub_{task_id}")

            with patch("app.api.tasks.history_manager", history), patch(
                "app.api.tasks.settings.UPLOAD_DIR", upload
            ), patch("app.api.tasks.settings.OUTPUT_DIR", output), patch(
                "app.api.tasks.settings.TEMP_DIR", temp
            ), patch("app.api.tasks._ALLOWED_DELETE_DIRS", (upload, output, temp)):
                result = asyncio.run(delete_task(task_id))

            self.assertEqual(result["status"], "deleted")
            self.assertIsNone(history.get_task(task_id))
            self.assertTrue(all(not path.exists() for path in files.values()))
            self.assertFalse((temp / task_id).exists())
            self.assertFalse((temp / f"dub_{task_id}").exists())


if __name__ == "__main__": unittest.main()
