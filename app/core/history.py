import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class HistoryManager:
    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = history_file or settings.BASE_DIR / "history.json"
        self._init_history_file()

    def _init_history_file(self):
        if not self.history_file.exists():
            self._write_history([])

    def _read_history(self) -> List[Dict[str, Any]]:
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    def _write_history(self, data: List[Dict[str, Any]]):
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

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
        history = self._read_history()
        for task in history:
            if task["task_id"] == task_id:
                task.update(updates)
                break
        self._write_history(history)

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        return self._read_history()

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        history = self._read_history()
        for task in history:
            if task["task_id"] == task_id:
                return task
        return None

    def delete_task(self, task_id: str) -> bool:
        """Remove a task from history. Returns True if found and removed."""
        history = self._read_history()
        original_len = len(history)
        history = [t for t in history if t["task_id"] != task_id]
        if len(history) == original_len:
            return False
        self._write_history(history)
        return True

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
            status = task.get("status")
            if status in {
                "pending",
                "uploading",
                "extracting_audio",
                "waiting_for_gpu",
                "switching_model",
                "pipeline_transcribing",
                "pipeline_translating",
                "pipeline_translated",
                "pipeline_dubbing",
                "transcribing",
                "translating",
            }:
                task["status"] = (
                    "translation_failed" if status == "translating" else "failed"
                )
                task["message"] = "任务因应用重启而中断，请重试。"
                task["interrupted_status"] = status
                task["interrupted_at"] = now
                if status.startswith("pipeline_"):
                    task["pipeline_status"] = "failed"
                    task["failed_stage"] = status
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

        if recovered:
            self._write_history(history)
            logger.warning("Recovered %s interrupted task stage(s)", recovered)
        return recovered


history_manager = HistoryManager()
