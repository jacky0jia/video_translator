import asyncio
import copy
from pathlib import Path
import shutil
import subprocess
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.pipeline_service import PipelineService


@pytest.mark.parametrize("old_status", ["failed", "completed", "cancelled"])
def test_start_replaces_terminal_snapshot_before_coroutine_gets_a_turn(monkeypatch, old_status):
    async def scenario():
        import app.services.pipeline_service as module
        saved = {"status": old_status, "pipeline_status": old_status, "pipeline_stage": "pipeline_transcribing",
                 "gpu_stage": "asr", "failed_stage": "pipeline_dubbing", "message": "old failure",
                 "dubbing_status": "completed", "dubbing_audio_path": "cached.wav"}
        history = Mock()
        history.update_task.side_effect = lambda _task, data: saved.update(data)
        monkeypatch.setattr(module, "history_manager", history)
        event = Mock()
        monkeypatch.setattr(module, "emit_event", event)
        service = PipelineService()
        service.run = AsyncMock()
        job = service.start("one", "Chinese", "voice")
        service.run.assert_not_awaited()
        assert saved["pipeline_status"] == "processing"
        assert saved["status"] == "pipeline_starting"
        assert saved["gpu_stage"] is None and saved["pipeline_stage"] is None
        assert saved["failed_stage"] is None and saved["progress_percent"] == 0
        assert saved["dubbing_status"] == "completed"  # keep valid reuse metadata
        assert saved["dubbing_audio_path"] == "cached.wav"
        snapshot = copy.deepcopy(saved)
        with pytest.raises(RuntimeError, match="already running"):
            service.start("one", "Chinese", "voice")
        assert saved == snapshot
        assert history.update_task.call_count == 1
        event.assert_called_once()
        await job
    asyncio.run(scenario())


def test_failed_start_persistence_does_not_launch_background_work(monkeypatch):
    async def scenario():
        import app.services.pipeline_service as module
        history = Mock()
        history.update_task.side_effect = OSError("history unavailable")
        monkeypatch.setattr(module, "history_manager", history)
        service = PipelineService()
        service.run = AsyncMock()
        with pytest.raises(OSError, match="history unavailable"):
            service.start("one", "Chinese", "voice")
        service.run.assert_not_called()
        assert not service._tasks
    asyncio.run(scenario())


def test_sse_hook_ignores_callbacks_from_closed_or_replaced_connections():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not available")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--test", str(root / "tests/task-sse-lifecycle.test.cjs")],
                   cwd=root / "app/frontend", check=True, capture_output=True, encoding="utf-8", timeout=30)
