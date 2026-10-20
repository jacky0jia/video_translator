import asyncio
import threading
import pytest
from fastapi import HTTPException

from app.api import tasks as api
from app.core.history import HistoryManager


@pytest.fixture
def case(tmp_path, monkeypatch):
    upload, output, temp = (tmp_path / name for name in ('uploads', 'output', 'temp'))
    for folder in (upload, output, temp):
        folder.mkdir()
    history = HistoryManager(tmp_path / 'history.json')
    ids, files = [], []
    for name in ('idle', 'candidate'):
        source = upload / f'{name}.mp4'
        source.write_bytes(b'owned test video')
        task_id = history.create_task(source.name, 'upload', str(source))
        subtitle = output / f'{name}.json'
        subtitle.write_bytes(b'owned test subtitle')
        task_temp = temp / task_id
        task_temp.mkdir()
        part = task_temp / 'part.wav'
        part.write_bytes(b'owned test audio')
        history.update_task(task_id, {'status': 'completed', 'transcription_path': str(subtitle)})
        ids.append(task_id)
        files.extend([source, subtitle, part])
    monkeypatch.setattr(api, 'history_manager', history)
    monkeypatch.setattr(api.settings, 'UPLOAD_DIR', upload)
    monkeypatch.setattr(api.settings, 'OUTPUT_DIR', output)
    monkeypatch.setattr(api.settings, 'TEMP_DIR', temp)
    monkeypatch.setattr(api, '_ALLOWED_DELETE_DIRS', (upload, output, temp))
    monkeypatch.setattr(api.pipeline_service, '_tasks', {})
    monkeypatch.setattr(api.pipeline_service, '_workers', {})
    monkeypatch.setattr(api, 'translation_busy', lambda _task: False)
    return history, ids, files


async def delete(mode, task_id, ids):
    if mode == 'single':
        return await api.delete_task(task_id)
    if mode == 'batch':
        return await api.batch_delete_tasks(api.BatchDeleteRequest(task_ids=ids))
    return await api.delete_all_tasks()


BUSY = [
    {'status': value} for value in (
        'pending', 'uploading', 'downloading', 'extracting_audio', 'transcribing',
        'translating', 'dubbing', 'burning', 'waiting_for_gpu', 'switching_model',
        'pipeline_starting', 'pipeline_transcribing', 'pipeline_translating',
        'pipeline_translated', 'pipeline_dubbing', 'pipeline_rendering', 'pipeline_cancelling',
    )
] + [
    {'pipeline_status': 'processing'}, {'pipeline_status': 'cancelling'},
    {'pipeline_cleanup_pending': True}, {'burn_status': 'processing'},
    {'dubbing_status': 'processing'},
]


@pytest.mark.parametrize('mode', ['single', 'batch', 'all'])
@pytest.mark.parametrize('busy', BUSY)
def test_busy_deletion_rejects_before_any_record_or_file_is_removed(case, mode, busy):
    history, ids, files = case
    history.update_task(ids[1], busy)
    before = history.history_file.read_bytes()
    with pytest.raises(HTTPException) as error:
        asyncio.run(delete(mode, ids[1], ids))
    assert error.value.status_code == 409
    assert history.history_file.read_bytes() == before
    assert all(path.is_file() for path in files)


@pytest.mark.parametrize('mode', ['single', 'batch', 'all'])
def test_live_retiring_worker_blocks_deletion_even_with_terminal_history(case, mode):
    async def scenario():
        history, ids, files = case
        worker = asyncio.get_running_loop().create_future()
        api.pipeline_service._workers[ids[1]] = {worker}
        before = history.history_file.read_bytes()
        try:
            with pytest.raises(HTTPException) as error:
                await delete(mode, ids[1], ids)
            assert error.value.status_code == 409
            assert history.history_file.read_bytes() == before
            assert all(path.is_file() for path in files)
            assert not worker.done()
            worker.set_result(None)
            await delete(mode, ids[1], ids)
            assert history.get_task(ids[1]) is None
            assert not files[-1].exists()
        finally:
            if not worker.done():
                worker.cancel()
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['single', 'batch', 'all'])
def test_translation_reservation_blocks_deletion(case, monkeypatch, mode):
    history, ids, files = case
    monkeypatch.setattr(api, 'translation_busy', lambda task_id: task_id == ids[1])
    before = history.history_file.read_bytes()
    with pytest.raises(HTTPException) as error:
        asyncio.run(delete(mode, ids[1], ids))
    assert error.value.status_code == 409
    assert history.history_file.read_bytes() == before
    assert all(path.is_file() for path in files)


@pytest.mark.parametrize('mode', ['single', 'batch', 'all'])
@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled', 'awaiting_review',
                                 'awaiting_compression', 'awaiting_dubbing_edit'])
def test_idle_or_manual_wait_tasks_can_still_be_deleted(case, mode, state):
    history, ids, files = case
    history.update_task(ids[1], {'status': state, 'pipeline_status': state})
    asyncio.run(delete(mode, ids[1], ids))
    assert history.get_task(ids[1]) is None
    assert not any(path.exists() for path in files[3:])
    if mode == 'single':
        assert history.get_task(ids[0]) is not None
        assert all(path.is_file() for path in files[:3])


def test_single_missing_task_still_returns_not_found(case):
    history, _ids, files = case
    before = history.history_file.read_bytes()
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.delete_task('missing'))
    assert error.value.status_code == 404
    assert history.history_file.read_bytes() == before
    assert all(path.is_file() for path in files)


@pytest.mark.parametrize('mode', ['single', 'batch', 'all'])
@pytest.mark.parametrize('stage,status', [('dubbing', 'dubbing'), ('burn', 'burning')])
@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled'])
def test_finished_standalone_stage_with_legacy_main_status_can_be_deleted(case, mode, stage, status, terminal):
    history, ids, files = case
    history.update_task(ids[1], {'status': status, f'{stage}_status': terminal, 'pipeline_status': None})
    asyncio.run(delete(mode, ids[1], ids))
    assert history.get_task(ids[1]) is None
    assert not any(path.exists() for path in files[3:])


@pytest.mark.parametrize('mode', ['single', 'batch', 'all'])
def test_dubbing_cleanup_must_finish_even_after_completed_flag(case, monkeypatch, mode):
    from app.services.dubbing_service import dubbing_service
    history, ids, files = case
    history.update_task(ids[1], {'dubbing_status': 'completed'})
    stopped = threading.Event()
    monkeypatch.setattr(dubbing_service, '_finished_events', {ids[1]: stopped})
    before = history.history_file.read_bytes()
    with pytest.raises(HTTPException) as error:
        asyncio.run(delete(mode, ids[1], ids))
    assert error.value.status_code == 409
    assert history.history_file.read_bytes() == before
    assert all(path.is_file() for path in files)
    assert not stopped.is_set()
    stopped.set()
    asyncio.run(delete(mode, ids[1], ids))
    assert history.get_task(ids[1]) is None
