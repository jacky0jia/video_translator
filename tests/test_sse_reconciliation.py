import asyncio
import json

import pytest

from app.core import events


def payload(value):
    return json.loads(value.removeprefix("data: "))


def test_disconnect_immediately_after_initial_snapshot_releases_queue():
    async def scenario():
        stream = events.event_generator("initial-close", {"status": "translating"})
        await anext(stream)
        assert "initial-close" in events._task_queues
        await stream.aclose()
        assert "initial-close" not in events._task_queues
    asyncio.run(scenario())


def test_snapshot_is_loaded_after_queue_registration_not_from_old_http_read():
    async def scenario():
        def latest():
            assert "late-snapshot" in events._task_queues
            return {"status": "completed", "progress_percent": 100}
        stream = events.event_generator("late-snapshot", {"status": "translating"}, snapshot_loader=latest)
        assert payload(await anext(stream))["status"] == "completed"
        await stream.aclose()
        assert "late-snapshot" not in events._task_queues
    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", ["completed", "failed", "translation_failed", "cancelled"])
def test_heartbeat_recovers_a_terminal_change_without_an_event(monkeypatch, terminal):
    monkeypatch.setattr(events.settings, "SSE_KEEPALIVE_TIMEOUT", 0.001)
    async def scenario():
        state = {"status": "translating"}
        stream = events.event_generator("silent-finish", snapshot_loader=lambda: dict(state))
        await anext(stream)
        state.update(status=terminal, progress_percent=100)
        assert payload(await asyncio.wait_for(anext(stream), 1))["status"] == terminal
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert "silent-finish" not in events._task_queues
    asyncio.run(scenario())


def test_intermediate_transcribed_snapshot_does_not_close_active_pipeline(monkeypatch):
    monkeypatch.setattr(events.settings, "SSE_KEEPALIVE_TIMEOUT", 0.001)
    async def scenario():
        stream = events.event_generator("intermediate", snapshot_loader=lambda: {
            "status": "transcribed", "pipeline_status": "processing",
        })
        await anext(stream)
        assert payload(await anext(stream))["pipeline_status"] == "processing"
        events.emit_event("intermediate", {"status": "dubbing"})
        assert payload(await anext(stream))["status"] == "dubbing"
        await stream.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("deleted_later", [False, True])
def test_deleted_task_reports_not_found_and_cleans_up(monkeypatch, deleted_later):
    monkeypatch.setattr(events.settings, "SSE_KEEPALIVE_TIMEOUT", 0.001)
    async def scenario():
        current = {"status": "translating"} if deleted_later else None
        stream = events.event_generator("removed", snapshot_loader=lambda: current)
        if deleted_later:
            await anext(stream)
            current = None
        assert (await anext(stream)).startswith("event: not_found")
        await stream.aclose()
        assert "removed" not in events._task_queues
    asyncio.run(scenario())


def test_snapshot_read_error_also_releases_the_subscription():
    async def scenario():
        def unavailable():
            raise OSError("history unavailable")
        stream = events.event_generator("read-error", snapshot_loader=unavailable)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert "read-error" not in events._task_queues
    asyncio.run(scenario())


def test_api_stream_rereads_history_when_the_body_starts(monkeypatch):
    from app.api import tasks
    from unittest.mock import Mock
    history = Mock()
    history.get_task.side_effect = [{"status": "burning"}, {
        "status": "failed", "burn_status": "failed", "burn_error": "encoder failed",
    }]
    monkeypatch.setattr(tasks, "history_manager", history)
    async def scenario():
        response = await tasks.task_events("api-snapshot")
        initial = payload(await anext(response.body_iterator))
        assert initial["status"] == "failed"
        assert initial["burn_error"] == "encoder failed"
        await response.body_iterator.aclose()
        assert "api-snapshot" not in events._task_queues
    asyncio.run(scenario())
