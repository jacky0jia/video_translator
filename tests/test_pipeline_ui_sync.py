import unittest
from pathlib import Path

from app.api.tasks import _task_event_snapshot


ROOT = Path(__file__).resolve().parents[1]


class PipelineUiSyncTests(unittest.TestCase):
    def test_sse_snapshot_keeps_pipeline_state_for_reconnect(self):
        snapshot = _task_event_snapshot({
            "status": "pipeline_translating",
            "message": "正在翻译...",
            "progress_percent": 40,
            "pipeline_status": "processing",
            "pipeline_stage": "pipeline_translating",
            "dubbing_status": "processing",
            "ignored": "private value",
        })

        self.assertEqual(snapshot["pipeline_status"], "processing")
        self.assertEqual(snapshot["pipeline_stage"], "pipeline_translating")
        self.assertEqual(snapshot["progress_percent"], 40)
        self.assertEqual(snapshot["dubbing_status"], "processing")
        self.assertNotIn("ignored", snapshot)

    def test_frontend_locks_duplicate_start_and_keeps_tracking_conflict(self):
        hook = (ROOT / "app/frontend/src/hooks/useTaskProcessing.js").read_text(encoding="utf-8")
        tasks_api = (ROOT / "app/api/tasks.py").read_text(encoding="utf-8")

        self.assertIn("const startRequestRef = useRef(false)", hook)
        self.assertIn("if (startRequestRef.current || running", hook)
        self.assertIn("startRequestRef.current = true", hook)
        self.assertIn("if (transcriptionCompletion)", hook)
        self.assertIn("if (task?.transcription_path || event.pipeline_status === 'processing') return", hook)
        self.assertIn("error.httpStatus === 409", hook)
        self.assertIn("setRunning(true)", hook)
        self.assertIn("startRequestRef.current = false", hook)
        self.assertIn("if (!persistedRunning || running || ['completed', 'failed', 'cancelled', 'awaiting_compression'].includes(status)) return", hook)
        self.assertIn("const restored = deriveTaskProcessingState(task, t)", hook)
        self.assertIn("initial_data=_task_event_snapshot(task)", tasks_api)


if __name__ == "__main__":
    unittest.main()
