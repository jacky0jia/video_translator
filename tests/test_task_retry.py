import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import BackgroundTasks

from app.api.tasks import retry_task


class History:
    def __init__(self, task):
        self.task = task

    def get_task(self, task_id):
        return self.task if self.task["task_id"] == task_id else None

    def update_task(self, task_id, updates):
        self.task.update(updates)


class TaskRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retranscribe_invalidates_all_downstream_provider_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.touch()
            history = History(
                {
                    "task_id": "one",
                    "source_path": str(source),
                    "filename": source.name,
                    "language": "English",
                    "status": "failed",
                    "transcription_path": "old-asr.json",
                    "translation_path": "old-translation.json",
                    "translations": {"Japanese": "old-ja.json"},
                    "translation_profiles": {"Japanese": "dubbing"},
                    "translation_alignment_review": {"Japanese": {"rows": [205, 217], "check_incomplete": True}},
                    "subtitle_outputs": {"srt": "old.srt"},
                    "pipeline_translation_targets": ["Japanese"],
                    "pipeline_status": "failed",
                    "pipeline_stage": "pipeline_dubbing",
                    "failed_stage": "pipeline_dubbing",
                    "burn_status": "completed",
                    "burn_path": "old-burn.mp4",
                    "dubbing_status": "failed",
                    "dubbing_audio_path": "old.wav",
                    "dubbing_raw_video_path": "old-raw.mp4",
                    "dubbing_video_path": "old-dubbed.mp4",
                    "dubbing_error": "wrong provider failed",
                    "dubbing_target_lang": "Japanese",
                    "dubbing_voice": "jf_alpha",
                    "dubbing_speed": 1.2,
                }
            )
            background = BackgroundTasks()

            with patch("app.api.tasks.history_manager", history), patch(
                "app.api.tasks.emit_event"
            ), patch("app.api.transcribe.run_transcription_task"):
                result = await retry_task("one", background)

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(background.tasks), 1)
        self.assertEqual(history.task["status"], "pending")
        self.assertEqual(history.task["translations"], {})
        self.assertEqual(history.task["translation_profiles"], {})
        self.assertEqual(history.task["translation_alignment_review"], {})
        self.assertEqual(history.task["subtitle_outputs"], {})
        self.assertEqual(history.task["pipeline_translation_targets"], [])
        for key in (
            "transcription_path",
            "translation_path",
            "pipeline_status",
            "pipeline_stage",
            "failed_stage",
            "burn_status",
            "burn_path",
            "dubbing_status",
            "dubbing_audio_path",
            "dubbing_raw_video_path",
            "dubbing_video_path",
            "dubbing_error",
            "dubbing_target_lang",
            "dubbing_voice",
            "dubbing_speed",
        ):
            self.assertIsNone(history.task[key], key)


if __name__ == "__main__":
    unittest.main()
