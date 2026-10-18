import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.task_run import current_task_run

logger = logging.getLogger(__name__)


def _next_revision(task: dict) -> int:
    value = task.get("state_revision")
    return (value if type(value) is int and value >= 0 else 0) + 1

_history_locks: dict[str, threading.RLock] = {}
_history_locks_guard = threading.Lock()


def _serialized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


class HistoryManager:
    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = history_file or settings.BASE_DIR / "history.json"
        # All managers of the same file share a process-local lock. This covers
        # the entire read/modify/write transaction, not only the final replace.
        key = os.path.normcase(str(self.history_file.resolve()))
        with _history_locks_guard:
            self._lock = _history_locks.setdefault(key, threading.RLock())
        self._init_history_file()

    @_serialized
    def _init_history_file(self):
        if not self.history_file.exists():
            self._write_history([])

    @_serialized
    def _read_history(self) -> List[Dict[str, Any]]:
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or any(
                not isinstance(task, dict) or not isinstance(task.get("task_id"), str)
                for task in data
            ):
                raise ValueError("Invalid history structure")
            return data
        except (ValueError, OSError) as exc:
            # Do not turn a damaged file into an empty list and then overwrite
            # it on the next update, silently losing every existing task.
            raise RuntimeError(
                "History file is unreadable or invalid; original file was preserved. "
                "Restore a backup before retrying."
            ) from exc

    @_serialized
    def _write_history(self, data: List[Dict[str, Any]]):
        fd, name = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=self.history_file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.history_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @_serialized
    def create_task(
        self,
        filename: str,
        source_type: str,
        source_path: Optional[str] = None,
        target_lang: Optional[str] = None,
    ) -> str:
        """Creates a new task record and returns the task_id."""
        history = self._read_history()
        task_id = str(uuid.uuid4())[:8]

        new_task = {
            "task_id": task_id,
            "filename": filename,
            "source_type": source_type,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "source_path": source_path,
            "upload_path": source_path,  # kept for backward compatibility
            "transcription_path": None,
            "translation_path": None,
            "target_lang": target_lang,
            "translations": {},
            "subtitle_outputs": {},
        }

        history.append(new_task)
        self._write_history(history)
        return task_id

    def update_task(self, task_id: str, updates: Dict[str, Any]):
        """Updates a specific task by id."""
        run = current_task_run(task_id)
        if run:
            # Always acquire run -> history, including terminal publication.
            with run.lock:
                return self._update_task(task_id, updates, run)
        return self._update_task(task_id, updates)

    @_serialized
    def _update_task(self, task_id: str, updates: Dict[str, Any], run=None):
        history = self._read_history()
        for task in history:
            if task["task_id"] == task_id:
                if run and not run.accepts(task):
                    logger.debug("Ignored obsolete pipeline write for task %s", task_id)
                    return False
                revision = _next_revision(task)
                task.update(updates)
                task["state_revision"] = revision
                self._write_history(history)
                return dict(task)

    def record_run_event(self, task_id: str, data: Dict[str, Any]):
        """Persist an owned event before assigning the version sent to clients."""
        updates = {key: value for key, value in data.items() if key not in {"progress", "state_revision"}}
        if "progress" in data:
            updates["progress_percent"] = data["progress"]
        return self.update_task(task_id, updates)

    @_serialized
    def finish_pipeline_cleanup(self, task_id: str, run_id: str):
        """Only the matching retired run may unlock its pending cleanup."""
        history = self._read_history()
        for task in history:
            if (task["task_id"] == task_id and task.get("pipeline_run_id") == run_id
                    and task.get("pipeline_cleanup_pending") is True):
                task.update({"status": "cancelled", "pipeline_status": "cancelled",
                             "pipeline_cleanup_pending": False, "message": "流水线已取消"})
                task["state_revision"] = _next_revision(task)
                self._write_history(history)
                return dict(task)

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        return self._read_history()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        history = self._read_history()
        for task in history:
            if task["task_id"] == task_id:
                return task
        return None

    @_serialized
    def delete_task(self, task_id: str) -> bool:
        """Remove a task from history. Returns True if found and removed."""
        history = self._read_history()
        original_len = len(history)
        history = [t for t in history if t["task_id"] != task_id]
        if len(history) == original_len:
            return False
        self._write_history(history)
        return True

    @_serialized
    def recover_interrupted_tasks(self) -> int:
        """Mark persisted in-progress work as interrupted after app restart.

        Background tasks are process-local and cannot still be running when a
        new FastAPI lifespan begins.  Leaving their persisted states unchanged
        makes a freshly opened UI display progress that does not exist.
        """
        history = self._read_history()
        recovered = 0
        now = datetime.now().isoformat()

        for task in history:
            before = dict(task)
            if task.get("pipeline_cleanup_pending") or task.get("pipeline_status") == "cancelling":
                # No old process-local worker survives an application restart.
                task.update({"status": "cancelled", "pipeline_status": "cancelled",
                             "pipeline_cleanup_pending": False, "message": "流水线已取消"})
                for stage in ("dubbing", "burn"):
                    if task.get(f"{stage}_status") == "processing":
                        task[f"{stage}_status"] = "cancelled"
                recovered += 1
            status = task.get("status")
            pipeline_running = task.get("pipeline_status") == "processing"
            if pipeline_running or status in {
                "pending",
                "uploading",
                "extracting_audio",
                "waiting_for_gpu",
                "switching_model",
                "pipeline_transcribing",
                "pipeline_translating",
                "pipeline_translated",
                "pipeline_dubbing",
                "pipeline_rendering",
                "dubbing",
                "burning",
                "transcribing",
                "translating",
            }:
                task["status"] = (
                    "translation_failed" if status == "translating" else "failed"
                )
                task["message"] = "任务因应用重启而中断，请重试。"
                task["interrupted_status"] = status
                task["interrupted_at"] = now
                if pipeline_running or str(status).startswith("pipeline_"):
                    task["pipeline_status"] = "failed"
                    task["failed_stage"] = task.get("pipeline_stage") or status
                task.pop("progress_percent", None)
                recovered += 1

            if task.get("burn_status") == "processing":
                task["burn_status"] = "failed"
                task["burn_error"] = "字幕嵌入因应用重启而中断，请重试。"
                recovered += 1

            if task.get("dubbing_status") == "processing":
                task["dubbing_status"] = "failed"
                task["dubbing_error"] = "配音因应用重启而中断，请重试。"
                recovered += 1

            if task != before:
                task["state_revision"] = _next_revision(task)

        if recovered:
            self._write_history(history)
            logger.warning("Recovered %s interrupted task stage(s)", recovered)
        return recovered


history_manager = HistoryManager()
