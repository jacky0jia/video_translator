import asyncio
import threading

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import tasks as api
from app.core.history import HistoryManager


@pytest.fixture
def case(tmp_path, monkeypatch):
    history = HistoryManager(tmp_path / 'history.json')
    paths = {}
    for name in ('source.mp4', 'source.json', 'target.json', 'dub.wav'):
        path = tmp_path / name
        path.write_bytes(b'preserve existing artifact')
        paths[name] = path
    task_id = history.create_task('source.mp4', 'upload', str(paths['source.mp4']))
    history.update_task(task_id, {
        'status': 'completed', 'transcription_path': str(paths['source.json']),
        'translation_path': str(paths['target.json']),
        'translations': {'Chinese': str(paths['target.json'])},
        'dubbing_audio_path': str(paths['dub.wav']), 'dubbing_status': 'completed',
    })
    events = []
    monkeypatch.setattr(api, 'history_manager', history)
    monkeypatch.setattr(api, 'emit_event', lambda *args: events.append(args))
    monkeypatch.setattr(api.pipeline_service, '_tasks', {})
    monkeypatch.setattr(api.pipeline_service, '_workers', {})
    monkeypatch.setattr(api, 'translation_busy', lambda _task: False)
    return history, task_id, paths, events


def assert_rejected_without_changes(case):
    history, task_id, paths, events = case
    before = history.history_file.read_bytes()
    artifacts = {name: path.read_bytes() for name, path in paths.items()}
    background = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.retry_task(task_id, background))
    assert error.value.status_code == 409
    assert history.history_file.read_bytes() == before
    assert {name: path.read_bytes() for name, path in paths.items()} == artifacts
    assert not background.tasks
    assert not events


@pytest.mark.parametrize('busy', [
    {'status': 'pending'}, {'status': 'transcribing'}, {'status': 'translating'},
    {'status': 'extracting_audio'}, {'status': 'waiting_for_gpu'},
    {'status': 'switching_model'}, {'status': 'downloading'},
    {'burn_status': 'processing'}, {'dubbing_status': 'processing'},
    {'pipeline_status': 'processing'}, {'pipeline_status': 'cancelling'},
    {'pipeline_cleanup_pending': True},
])
def test_busy_retry_does_not_clear_artifacts_or_queue_another_transcription(case, busy):
    history, task_id, _paths, _events = case
    history.update_task(task_id, busy)
    assert_rejected_without_changes(case)


def test_translation_reservation_blocks_retry(case, monkeypatch):
    monkeypatch.setattr(api, 'translation_busy', lambda _task: True)
    assert_rejected_without_changes(case)


def test_dubbing_cleanup_blocks_retry_until_it_really_stops(case, monkeypatch):
    from app.services.dubbing_service import dubbing_service
    history, task_id, _paths, _events = case
    stopped = threading.Event()
    monkeypatch.setattr(dubbing_service, '_finished_events', {task_id: stopped})
    assert_rejected_without_changes(case)
    stopped.set()
    background = BackgroundTasks()
    asyncio.run(api.retry_task(task_id, background))
    assert len(background.tasks) == 1
    assert history.get_task(task_id)['status'] == 'pending'


@pytest.mark.parametrize('registry', ['_tasks', '_workers'])
def test_pipeline_execution_or_retiring_worker_blocks_retry(case, registry):
    async def scenario():
        history, task_id, paths, events = case
        worker = asyncio.get_running_loop().create_future()
        getattr(api.pipeline_service, registry)[task_id] = worker if registry == '_tasks' else {worker}
        before = history.history_file.read_bytes()
        background = BackgroundTasks()
        try:
            with pytest.raises(HTTPException) as error:
                await api.retry_task(task_id, background)
            assert error.value.status_code == 409
            assert history.history_file.read_bytes() == before
            assert all(path.is_file() for path in paths.values())
            assert not background.tasks and not events
        finally:
            worker.cancel()
    asyncio.run(scenario())


@pytest.mark.parametrize('state', [
    {'status': 'failed'}, {'status': 'completed'}, {'status': 'cancelled'},
    {'status': 'awaiting_review', 'pipeline_status': 'awaiting_review'},
    {'status': 'awaiting_compression', 'pipeline_status': 'awaiting_compression'},
    {'status': 'awaiting_dubbing_edit', 'pipeline_status': 'awaiting_dubbing_edit'},
    {'status': 'dubbing', 'dubbing_status': 'completed'},
    {'status': 'burning', 'burn_status': 'completed'},
])
def test_idle_retry_queues_one_transcription_and_does_not_delete_old_files(case, state):
    history, task_id, paths, events = case
    history.update_task(task_id, state)
    background = BackgroundTasks()
    result = asyncio.run(api.retry_task(task_id, background))
    assert result['status'] == 'success'
    assert len(background.tasks) == 1
    assert background.tasks[0].args[:3] == (task_id, str(paths['source.mp4']), 'source.mp4')
    saved = history.get_task(task_id)
    assert saved['status'] == 'pending'
    assert saved['transcription_path'] is None and saved['translation_path'] is None
    assert all(path.read_bytes() == b'preserve existing artifact' for path in paths.values())
    assert len(events) == 1


def test_second_retry_before_worker_starts_is_rejected(case):
    async def scenario():
        history, task_id, _paths, events = case
        background = BackgroundTasks()
        await api.retry_task(task_id, background)
        before = history.history_file.read_bytes()
        with pytest.raises(HTTPException) as error:
            await api.retry_task(task_id, background)
        assert error.value.status_code == 409
        assert history.history_file.read_bytes() == before
        assert len(background.tasks) == 1 and len(events) == 1
    asyncio.run(scenario())
