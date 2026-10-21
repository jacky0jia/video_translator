import asyncio

import pytest

from app.api import dubbing as api
from app.core import events
from app.core.history import HistoryManager


@pytest.fixture
def standalone_task(tmp_path, monkeypatch):
    history = HistoryManager(tmp_path / "history.json")
    task_id = history.create_task("video.mp4", "upload", str(tmp_path / "video.mp4"))
    history.update_task(task_id, {
        "status": "dubbing",
        "dubbing_status": "processing",
        "progress_percent": 0,
        "message": "preparing",
    })
    monkeypatch.setattr(api, "history_manager", history)
    monkeypatch.setattr(events, "_task_queues", {})
    yield history, task_id
    events._task_queues.clear()


def _drain(queue):
    values = []
    while not queue.empty():
        values.append(queue.get_nowait())
    return values


def test_standalone_dubbing_success_persists_and_emits_completed_snapshot(standalone_task, monkeypatch):
    history, task_id = standalone_task

    def complete(*_args):
        saved = history.update_task(task_id, {
            "dubbing_status": "completed",
            "dubbing_audio_path": "audio.wav",
            "dubbing_video_path": "video.mp4",
        })
        events.emit_event(task_id, {"status": "dubbing_completed", "message": "配音完成", "progress": 100})
        return saved

    monkeypatch.setattr(api.dubbing_service, "dub", complete)

    async def run():
        queue = events._get_queue(task_id)
        api._run_dubbing_task(task_id, "Chinese", "voice", 1.0, None)
        return _drain(queue)

    emitted = asyncio.run(run())
    saved = history.get_task(task_id)
    assert saved["status"] == "completed"
    assert saved["dubbing_status"] == "completed"
    assert saved["progress_percent"] == 100
    assert emitted[-1]["status"] == "completed"
    assert emitted[-1]["dubbing_status"] == "completed"
    assert emitted[-1]["state_revision"] == saved["state_revision"]


def test_standalone_dubbing_failure_persists_and_emits_failed_snapshot(standalone_task, monkeypatch):
    history, task_id = standalone_task

    def fail(*_args):
        history.update_task(task_id, {"dubbing_status": "failed", "dubbing_error": "provider offline"})
        events.emit_event(task_id, {"status": "dubbing_failed", "message": "provider offline", "progress": 0})
        raise RuntimeError("provider offline")

    monkeypatch.setattr(api.dubbing_service, "dub", fail)

    async def run():
        queue = events._get_queue(task_id)
        api._run_dubbing_task(task_id, "Chinese", "voice", 1.0, None)
        return _drain(queue)

    emitted = asyncio.run(run())
    saved = history.get_task(task_id)
    assert saved["status"] == "failed"
    assert saved["dubbing_status"] == "failed"
    assert saved["message"] == "provider offline"
    assert emitted[-1]["status"] == "failed"
    assert emitted[-1]["dubbing_status"] == "failed"
    assert emitted[-1]["state_revision"] == saved["state_revision"]

