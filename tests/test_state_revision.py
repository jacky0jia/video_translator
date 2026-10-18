import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.history import HistoryManager
from app.core.task_run import TaskRun, task_run_scope


@pytest.fixture
def state(tmp_path, monkeypatch):
    import app.core.history as history_module
    import app.core.events as events
    history = HistoryManager(tmp_path / 'history.json')
    task_id = history.create_task('test.mp4', 'upload')
    history.update_task(task_id, {'pipeline_run_id': 'run', 'pipeline_status': 'processing'})
    monkeypatch.setattr(history_module, 'history_manager', history)
    monkeypatch.setattr(events, '_task_queues', {})
    return history, task_id


def decode(frame):
    return json.loads(frame.split('data: ', 1)[1])


def test_revision_is_server_owned_and_stale_write_does_not_advance_it(state):
    history, task_id = state
    before = history.get_task(task_id)['state_revision']
    saved = history.update_task(task_id, {'state_revision': -10, 'message': 'new'})
    assert saved['state_revision'] == before + 1
    with task_run_scope(TaskRun(task_id, 'old')):
        assert history.update_task(task_id, {'message': 'old'}) is False
    assert history.get_task(task_id) == saved


def test_live_event_progress_is_persisted_at_the_revision_it_carries(state):
    async def scenario():
        import app.core.events as events
        history, task_id = state
        queue = events._get_queue(task_id)
        with task_run_scope(TaskRun(task_id, 'run')):
            events.emit_event(task_id, {'status': 'dubbing', 'progress': 53, 'message': 'synthesizing'})
        event = queue.get_nowait()
        saved = history.get_task(task_id)
        assert event['state_revision'] == saved['state_revision']
        assert event['progress_percent'] == saved['progress_percent'] == 53
        assert event['status'] == saved['status'] == 'dubbing'
    asyncio.run(scenario())


def test_stream_coalesces_old_progress_and_terminal_to_the_latest_snapshot(state):
    async def scenario():
        import app.core.events as events
        history, task_id = state
        stream = events.event_generator(task_id, snapshot_loader=lambda: events.task_event_snapshot(history.get_task(task_id)))
        await anext(stream)
        with task_run_scope(TaskRun(task_id, 'run')):
            events.emit_event(task_id, {'status': 'dubbing', 'progress': 20})
            old = history.get_task(task_id)['state_revision']
            events.emit_event(task_id, {'status': 'dubbing', 'progress': 70})
            latest = decode(await asyncio.wait_for(anext(stream), 1))
            assert latest['progress_percent'] == 70
            events.emit_event(task_id, {'status': 'pipeline_rendering', 'pipeline_stage': 'pipeline_rendering', 'progress_percent': 0})
            next_stage = decode(await asyncio.wait_for(anext(stream), 1))
            assert next_stage['state_revision'] > latest['state_revision']
            assert next_stage['progress_percent'] == 0  # stage reset is legitimate
            events._emit_event(task_id, {'status': 'failed', 'pipeline_run_id': 'run', 'state_revision': old})
            events.emit_event(task_id, {'status': 'burning', 'progress': 10})
            current = decode(await asyncio.wait_for(anext(stream), 1))
            assert current['status'] == 'burning' and current['progress_percent'] == 10
        await stream.aclose()
        assert not events._task_queues
    asyncio.run(scenario())


def test_initial_snapshot_cannot_be_followed_by_its_older_queued_state(state):
    async def scenario():
        import app.core.events as events
        history, task_id = state
        first = True

        def snapshot():
            nonlocal first
            if first:
                first = False
                with task_run_scope(TaskRun(task_id, 'run')):
                    events.emit_event(task_id, {'status': 'dubbing', 'progress': 40})
            return events.task_event_snapshot(history.get_task(task_id))

        stream = events.event_generator(task_id, snapshot_loader=snapshot)
        initial = decode(await anext(stream))
        with task_run_scope(TaskRun(task_id, 'run')):
            events.emit_event(task_id, {'status': 'dubbing', 'progress': 80})
        later = decode(await asyncio.wait_for(anext(stream), 1))
        assert later['state_revision'] > initial['state_revision']
        assert later['progress_percent'] == 80
        await stream.aclose()
    asyncio.run(scenario())


def test_active_pipeline_transcribed_event_does_not_close_its_stream(state):
    async def scenario():
        import app.core.events as events
        history, task_id = state
        stream = events.event_generator(task_id, snapshot_loader=lambda: events.task_event_snapshot(history.get_task(task_id)))
        await anext(stream)
        with task_run_scope(TaskRun(task_id, 'run')):
            events.emit_event(task_id, {'status': 'transcribed', 'progress_percent': 100})
            assert decode(await anext(stream))['status'] == 'transcribed'
            events.emit_event(task_id, {'status': 'pipeline_translating', 'progress_percent': 0})
            assert decode(await asyncio.wait_for(anext(stream), 1))['status'] == 'pipeline_translating'
        await stream.aclose()
    asyncio.run(scenario())


def test_pending_cleanup_survives_sse_until_controller_finishes_it(state):
    async def scenario():
        import app.core.events as events
        history, task_id = state
        history.update_task(task_id, {'status': 'pipeline_cancelling', 'pipeline_status': 'cancelling', 'pipeline_cleanup_pending': True})
        stream = events.event_generator(task_id, snapshot_loader=lambda: events.task_event_snapshot(history.get_task(task_id)))
        assert decode(await anext(stream))['pipeline_cleanup_pending'] is True
        final = history.finish_pipeline_cleanup(task_id, 'run')
        events.emit_event(task_id, events.task_event_snapshot(final))
        result = decode(await asyncio.wait_for(anext(stream), 1))
        assert result['status'] == 'cancelled' and result['pipeline_cleanup_pending'] is False
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    asyncio.run(scenario())


def test_cleanup_callback_cannot_finish_another_run(state):
    history, task_id = state
    history.update_task(task_id, {'pipeline_run_id': 'new', 'pipeline_cleanup_pending': False, 'status': 'pipeline_starting'})
    before = history.get_task(task_id)
    assert history.finish_pipeline_cleanup(task_id, 'run') is None
    assert history.get_task(task_id) == before


def test_restart_releases_only_persisted_cancel_cleanup_and_advances_revision(state):
    history, task_id = state
    before = history.update_task(task_id, {'status': 'pipeline_cancelling', 'pipeline_status': 'cancelling',
                                         'pipeline_cleanup_pending': True, 'dubbing_status': 'cancelled'})
    assert history.recover_interrupted_tasks() == 1
    after = history.get_task(task_id)
    assert after['status'] == 'cancelled' and after['pipeline_cleanup_pending'] is False
    assert after['state_revision'] > before['state_revision']


@pytest.mark.parametrize('pending', [False, True])
def test_cancel_api_reports_real_cleanup_state(state, monkeypatch, pending):
    async def scenario():
        import app.api.pipeline as api
        history, task_id = state
        history.update_task(task_id, {'pipeline_cleanup_pending': pending})
        monkeypatch.setattr(api, 'history_manager', history)
        monkeypatch.setattr(api, 'pipeline_service', SimpleNamespace(cancel=AsyncMock(return_value=True)))
        result = await api.cancel_pipeline(task_id)
        assert result['status'] == ('cancelling' if pending else 'cancelled')
    asyncio.run(scenario())
