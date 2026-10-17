import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.core.history import HistoryManager


class HistoryTransactionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "history.json"
        self.manager = HistoryManager(self.path)
        self.task = self.manager.create_task("test.mp4", "upload")

    def test_concurrent_managers_preserve_all_field_updates(self):
        class SlowReader(HistoryManager):
            def _read_history(self):
                data = super()._read_history()
                time.sleep(0.005)
                return data

        managers = [SlowReader(self.path) for _ in range(8)]
        ready = threading.Barrier(len(managers))

        def update(index):
            ready.wait(timeout=5)
            managers[index].update_task(self.task, {f"field_{index}": index})

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(update, range(8)))
        task = self.manager.get_task(self.task)
        self.assertEqual([task[f"field_{i}"] for i in range(8)], list(range(8)))

    def test_concurrent_creates_do_not_drop_tasks(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(lambda i: self.manager.create_task(f"{i}.mp4", "upload"), range(16)))
        self.assertEqual({row["task_id"] for row in self.manager.get_all_tasks()}, {self.task, *ids})

    def test_atomic_replace_failure_preserves_old_history(self):
        before = self.path.read_bytes()
        with patch("app.core.history.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.manager.update_task(self.task, {"status": "completed"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".history-*.tmp")), [])

    def test_serialization_failure_preserves_old_history(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.manager.update_task(self.task, {"progress_percent": float("nan")})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".history-*.tmp")), [])

    def test_invalid_history_is_not_silently_overwritten(self):
        for content in (b"{broken", b"{}", b"[null]", b'[{}]'):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaisesRegex(RuntimeError, "original file was preserved"):
                    self.manager.create_task("new.mp4", "upload")
                self.assertEqual(self.path.read_bytes(), content)

    def test_missing_history_after_start_is_not_recreated_by_update(self):
        self.path.unlink()
        with self.assertRaisesRegex(RuntimeError, "original file was preserved"):
            self.manager.update_task(self.task, {"status": "completed"})
        self.assertFalse(self.path.exists())

    def test_deleted_task_is_not_resurrected_by_late_update(self):
        self.manager.delete_task(self.task)
        before = self.path.read_bytes()
        self.manager.update_task(self.task, {"status": "completed"})
        self.assertEqual(self.path.read_bytes(), before)

    def test_restart_recovers_pipeline_even_when_global_stage_is_stale(self):
        for status in ("pipeline_rendering", "switching_model", "completed"):
            with self.subTest(status=status):
                self.manager.update_task(self.task, {
                    "status": status, "pipeline_status": "processing",
                    "pipeline_stage": "pipeline_rendering", "burn_status": "processing",
                    "progress_percent": 90,
                })
                self.assertEqual(self.manager.recover_interrupted_tasks(), 2)
                task = self.manager.get_task(self.task)
                self.assertEqual(task["status"], "failed")
                self.assertEqual(task["pipeline_status"], "failed")
                self.assertEqual(task["burn_status"], "failed")
                self.assertEqual(task["failed_stage"], "pipeline_rendering")
                self.assertNotIn("progress_percent", task)
                self.assertEqual(self.manager.recover_interrupted_tasks(), 0)

    def test_human_waiting_state_and_existing_artifacts_survive_restart(self):
        self.manager.update_task(self.task, {
            "status": "awaiting_compression", "pipeline_status": "awaiting_compression",
            "compression_request": {"segment_indices": [2]}, "translation_path": "old.json",
        })
        before = self.manager.get_task(self.task)
        self.assertEqual(self.manager.recover_interrupted_tasks(), 0)
        self.assertEqual(self.manager.get_task(self.task), before)
