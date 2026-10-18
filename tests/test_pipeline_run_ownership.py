import asyncio
import copy
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.core.history import HistoryManager
from app.core.task_run import TaskRun, current_task_run, task_run_scope
from app.services.pipeline_service import PipelineService


@pytest.fixture
def owned_history(tmp_path, monkeypatch):
    import app.core.history as history_module
    import app.core.events as events
    import app.services.pipeline_service as pipeline
    import app.api.pipeline as api

    history = HistoryManager(tmp_path / 'history.json')
    task_id = history.create_task('fixture.mp4', 'upload')
    monkeypatch.setattr(history_module, 'history_manager', history)
    monkeypatch.setattr(pipeline, 'history_manager', history)
    monkeypatch.setattr(api, 'history_manager', history)
    monkeypatch.setattr(events, '_task_queues', {})
    return history, task_id


@pytest.mark.parametrize('updates', [
    {'status': 'dubbing', 'progress_percent': 90},
    {'status': 'failed', 'message': 'old error', 'dubbing_error': 'old error'},
    {'status': 'completed', 'pipeline_status': 'completed'},
    {'dubbing_video_path': 'old.mp4', 'translations': {'Chinese': 'old.json'}},
])
def test_old_run_cannot_write_progress_errors_or_output_references(owned_history, updates):
    history, task_id = owned_history
    history.update_task(task_id, {'pipeline_run_id': 'new', 'status': 'pipeline_starting'})
    before = history.get_task(task_id)
    with task_run_scope(TaskRun(task_id, 'old')):
        assert history.update_task(task_id, updates) is False
    assert history.get_task(task_id) == before


def test_revoked_run_cannot_write_even_before_the_next_run(owned_history):
    history, task_id = owned_history
    history.update_task(task_id, {'pipeline_run_id': 'old', 'status': 'cancelled'})
    with task_run_scope(TaskRun(task_id, 'old', active=False)):
        assert history.update_task(task_id, {'status': 'completed'}) is False
    assert history.get_task(task_id)['status'] == 'cancelled'


def test_run_context_reaches_worker_and_stamps_only_current_events(owned_history):
    async def scenario():
        import app.core.events as events
        history, task_id = owned_history
        history.update_task(task_id, {'pipeline_run_id': 'new'})
        queue = events._get_queue(task_id)
        payload = {'status': 'dubbing', 'progress': 33}

        def worker():
            assert current_task_run(task_id).run_id == 'new'
            history.update_task(task_id, {'progress_percent': 33})
            events.emit_event(task_id, payload)

        with task_run_scope(TaskRun(task_id, 'new')):
            await asyncio.to_thread(worker)
        event = await asyncio.wait_for(queue.get(), 1)
        assert event['pipeline_run_id'] == 'new'
        assert 'pipeline_run_id' not in payload  # never mutate the caller's event
        assert history.get_task(task_id)['progress_percent'] == 33
        with task_run_scope(TaskRun(task_id, 'old')):
            await asyncio.to_thread(events.emit_event, task_id, {'status': 'failed'})
        assert queue.empty()
    asyncio.run(scenario())


def test_queued_old_terminal_does_not_close_new_run_stream(owned_history):
    async def scenario():
        import app.core.events as events
        from app.api.tasks import _task_event_snapshot
        history, task_id = owned_history
        history.update_task(task_id, {'pipeline_run_id': 'new', 'status': 'pipeline_starting', 'pipeline_status': 'processing'})
        stream = events.event_generator(task_id, snapshot_loader=lambda: _task_event_snapshot(history.get_task(task_id)))
        initial = json.loads((await anext(stream)).split('data: ', 1)[1])
        assert initial['pipeline_run_id'] == 'new'
        events.emit_event(task_id, {'status': 'failed', 'pipeline_run_id': 'old'})
        events.emit_event(task_id, {'status': 'dubbing', 'progress': 44, 'pipeline_run_id': 'new'})
        current = json.loads((await asyncio.wait_for(anext(stream), 1)).split('data: ', 1)[1])
        assert current['status'] == 'dubbing' and current['progress'] == 44
        events.emit_event(task_id, {'status': 'completed', 'pipeline_run_id': 'new'})
        assert 'completed' in await anext(stream)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert task_id not in events._task_queues
    asyncio.run(scenario())


def test_only_accepted_start_persists_request_metadata_and_a_new_run_id(owned_history):
    async def scenario():
        history, task_id = owned_history
        service = PipelineService()
        service.run = AsyncMock()
        job = service.start(task_id, 'Chinese', 'voice', burn_subtitles=True, subtitle_format='ass',
                            end_note_enabled=True, output_options={'policy': 'codec'})
        accepted = history.get_task(task_id)
        assert accepted['video_output_options'] == {'policy': 'codec'}
        assert accepted['last_subtitle_format'] == 'ass'
        assert accepted['last_process_route'] == 'dubbing'
        assert accepted['dubbing_burn_subtitles'] and accepted['last_end_note_enabled']
        assert accepted['pipeline_run_id']
        with pytest.raises(RuntimeError, match='already running'):
            service.start(task_id, 'Japanese', 'different', output_options={'policy': 'custom'})
        assert history.get_task(task_id) == accepted
        await job
        next_job = service.start(task_id, 'Chinese', 'voice', output_options={'policy': 'resolution'})
        assert history.get_task(task_id)['pipeline_run_id'] != accepted['pipeline_run_id']
        await next_job
    asyncio.run(scenario())


def test_api_conflict_does_not_modify_accepted_output_choices(owned_history, monkeypatch):
    async def scenario():
        import app.api.pipeline as api
        history, task_id = owned_history
        history.update_task(task_id, {'video_output_options': {'policy': 'codec'}, 'last_end_note_enabled': False})
        before = history.get_task(task_id)
        monkeypatch.setattr(api, 'resolve_tts_route', lambda *_: SimpleNamespace(provider_id='speaches'))
        monkeypatch.setattr(api.pipeline_service, 'start', Mock(side_effect=RuntimeError('Pipeline already running')))
        with pytest.raises(HTTPException) as error:
            await api.start_pipeline(api.PipelineRequest(task_id=task_id, output_options={'policy': 'custom'}))
        assert error.value.status_code == 409
        assert history.get_task(task_id) == before
    asyncio.run(scenario())


def test_cancel_before_first_turn_records_cancelled_without_starting_work(owned_history, monkeypatch):
    async def scenario():
        from app.services.dubbing_service import dubbing_service
        history, task_id = owned_history
        monkeypatch.setattr(dubbing_service, 'request_cancel', lambda _: False)
        service = PipelineService()
        service.run = AsyncMock()
        service.start(task_id, 'Chinese', 'voice')
        assert await service.cancel(task_id)
        service.run.assert_not_awaited()
        assert history.get_task(task_id)['pipeline_status'] == 'cancelled'
        assert not service._tasks and not service._runs
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['dubbing', 'burn'])
@pytest.mark.parametrize('late_error', [False, True])
def test_cancelled_real_thread_cannot_publish_or_overlap_a_new_pipeline(owned_history, tmp_path, monkeypatch, stage, late_error):
    async def scenario():
        import app.core.events as events
        from app.services.dubbing_service import dubbing_service
        from app.services.video_burn_service import video_burn_service
        history, task_id = owned_history
        source = tmp_path / 'source.json'
        source.touch()
        raw = tmp_path / 'raw.mp4'
        raw.touch()
        history.update_task(task_id, {'transcription_path': str(source), 'translations': {'Chinese': str(source)},
                                     'dubbing_raw_video_path': str(raw)})
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        queue = events._get_queue(task_id)
        run_seen = []

        def worker(*_args):
            run_seen.append(current_task_run(task_id))
            history.update_task(task_id, {f'{stage}_status': 'processing'})
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise RuntimeError('Test did not release its worker')
            history.update_task(task_id, {'status': 'completed', 'pipeline_status': 'completed',
                                         f'{stage}_status': 'completed', 'dubbing_video_path': 'old-output.mp4'})
            events.emit_event(task_id, {'status': 'completed', 'message': 'late worker result'})
            if late_error:
                raise RuntimeError('late worker error')

        monkeypatch.setattr(dubbing_service, 'request_cancel', lambda _: True)
        monkeypatch.setattr(dubbing_service, 'wait_for_stop', lambda *_: False)  # reproduce timeout without waiting 20 seconds
        monkeypatch.setattr(dubbing_service, 'dub', worker)
        monkeypatch.setattr(video_burn_service, 'burn', worker)
        service = PipelineService()
        if stage == 'burn':
            monkeypatch.setattr(service, '_can_reuse_dubbing', lambda *_: True)
        service.start(task_id, 'Chinese', 'voice', burn_subtitles=stage == 'burn')
        pending_workers = []
        try:
            await asyncio.wait_for(entered.wait(), 2)
            pending_workers = list(service._workers[task_id])
            assert await service.cancel(task_id)
            cancelled = history.get_task(task_id)
            assert cancelled['pipeline_status'] == 'cancelling'
            assert cancelled['pipeline_cleanup_pending'] is True
            assert cancelled[f'{stage}_status'] == 'cancelled'
            queued = queue.qsize()
            with pytest.raises(RuntimeError, match='still stopping'):
                service.start(task_id, 'Chinese', 'voice', output_options={'policy': 'custom'})
            assert history.get_task(task_id) == cancelled
            release.set()
            await asyncio.gather(*pending_workers, return_exceptions=True)
            await asyncio.sleep(0)  # deliver any thread-scheduled queue callbacks
            assert history.get_task(task_id) == {
                **cancelled, 'status': 'cancelled', 'pipeline_status': 'cancelled',
                'pipeline_cleanup_pending': False, 'message': '流水线已取消',
                'state_revision': cancelled['state_revision'] + 1,
            }
            assert queue.qsize() == queued + 1  # only the controller's cleanup terminal
            while not queue.empty():
                assert queue.get_nowait().get('message') != 'late worker result'
            assert not service._workers
            service.run = AsyncMock()
            restarted = service.start(task_id, 'Chinese', 'voice')
            assert history.get_task(task_id)['pipeline_run_id'] != cancelled['pipeline_run_id']
            with task_run_scope(run_seen[0]):
                assert history.update_task(task_id, {'message': 'old retry continuation'}) is False
            await restarted
        finally:
            release.set()
            await asyncio.gather(*pending_workers, return_exceptions=True)
    asyncio.run(scenario())


def test_old_run_finalizer_cannot_unregister_a_newer_task(owned_history):
    async def scenario():
        history, task_id = owned_history
        entered = asyncio.Event()

        async def block(*_):
            entered.set()
            await asyncio.Event().wait()

        service = PipelineService(transcribe_stage=block)
        old = service.start(task_id, 'Chinese', 'voice')
        await entered.wait()
        newer = asyncio.create_task(asyncio.Event().wait())
        service._tasks[task_id] = newer
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        assert service._tasks[task_id] is newer
        newer.cancel()
        await asyncio.gather(newer, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('flag', ['burn_status', 'dubbing_status'])
def test_pipeline_does_not_start_over_an_existing_standalone_worker(owned_history, flag):
    async def scenario():
        history, task_id = owned_history
        history.update_task(task_id, {flag: 'processing'})
        before = copy.deepcopy(history.get_task(task_id))
        service = PipelineService()
        with pytest.raises(RuntimeError, match='processing'):
            service.start(task_id, 'Chinese', 'voice')
        assert history.get_task(task_id) == before
        assert not service._tasks
    asyncio.run(scenario())


@pytest.mark.parametrize('operation', ['burn', 'dub', 'retry', 'edit', 'translate', 'export'])
def test_other_mutating_routes_cannot_bypass_a_retiring_pipeline(owned_history, monkeypatch, operation):
    async def scenario():
        import app.api.burn as burn
        import app.api.dubbing as dubbing
        import app.api.tasks as tasks
        import app.api.translation as translation
        from app.services.pipeline_service import pipeline_service
        history, task_id = owned_history
        for module in (burn, dubbing, tasks, translation):
            monkeypatch.setattr(module, 'history_manager', history)
        pending = asyncio.get_running_loop().create_future()
        monkeypatch.setattr(pipeline_service, '_tasks', {})
        monkeypatch.setattr(pipeline_service, '_workers', {task_id: {pending}})
        before = history.get_task(task_id)
        background = BackgroundTasks()
        calls = {
            'burn': lambda: burn.burn_video(burn.BurnRequest(task_id=task_id), background),
            'dub': lambda: dubbing.start_dubbing(dubbing.DubbingRequest(task_id=task_id, voice='voice'), background),
            'retry': lambda: tasks.retry_task(task_id, background),
            'edit': lambda: tasks.edit_segment(task_id, 0, tasks.SegmentEdit(source_text='edited')),
            'translate': lambda: translation.translate_transcription(json_path='unused.json', target_lang='Chinese', task_id=task_id),
            'export': lambda: translation.export_subtitles(json_path='unused.json', task_id=task_id),
        }
        try:
            with pytest.raises(HTTPException) as error:
                await calls[operation]()
            assert error.value.status_code == 409
            assert history.get_task(task_id) == before
            assert not background.tasks
            pending.set_result(None)
            assert not pipeline_service.is_busy(task_id)
        finally:
            if not pending.done():
                pending.cancel()
    asyncio.run(scenario())


def test_pipeline_translation_context_is_not_blocked_by_its_own_busy_guard(owned_history, monkeypatch):
    async def scenario():
        import app.api.translation as api
        from app.services.pipeline_service import pipeline_service
        history, task_id = owned_history
        monkeypatch.setattr(api, 'history_manager', history)
        pending = asyncio.get_running_loop().create_future()
        monkeypatch.setattr(pipeline_service, '_workers', {task_id: {pending}})
        try:
            with task_run_scope(TaskRun(task_id, 'current')):
                with pytest.raises(HTTPException) as error:
                    await api.translate_transcription(json_path='missing.json', target_lang='Chinese', task_id=task_id, pipeline=True)
            assert error.value.status_code == 404  # passed ownership check, missing test file
        finally:
            pending.cancel()
    asyncio.run(scenario())


def test_dubbing_rechecks_pipeline_busy_after_voice_lookup(owned_history, tmp_path, monkeypatch):
    async def scenario():
        import app.api.dubbing as api
        from app.services.pipeline_service import pipeline_service
        history, task_id = owned_history
        source = tmp_path / 'source.mp4'
        source.touch()
        history.update_task(task_id, {'source_path': str(source), 'transcription_path': 'source.json'})
        monkeypatch.setattr(api, 'history_manager', history)
        monkeypatch.setattr(api, 'resolve_tts_route', lambda *_: SimpleNamespace(provider_id='qwen'))
        monkeypatch.setattr(pipeline_service, '_workers', {})
        monkeypatch.setattr(pipeline_service, '_tasks', {})
        pending = asyncio.get_running_loop().create_future()

        async def lookup(*_):
            pipeline_service._workers[task_id] = {pending}
            return 'voice'

        monkeypatch.setattr(api, 'resolve_available_voice', lookup)
        before = history.get_task(task_id)
        background = BackgroundTasks()
        try:
            with pytest.raises(HTTPException) as error:
                await api.start_dubbing(api.DubbingRequest(task_id=task_id, voice='voice'), background)
            assert error.value.status_code == 409
            assert not background.tasks
            assert history.get_task(task_id) == before
        finally:
            pending.cancel()
    asyncio.run(scenario())


def test_compression_approval_gets_its_state_from_the_owned_start(owned_history, monkeypatch):
    async def scenario():
        import app.api.pipeline as api
        history, task_id = owned_history
        history.update_task(task_id, {
            'pipeline_status': 'awaiting_compression', 'translation_path': 'original.json',
            'compression_request': {'target_lang': 'Chinese', 'voice': 'voice', 'speed': 1,
                                    'burn_subtitles': False, 'show_source': False, 'show_target': True,
                                    'subtitle_style': {}, 'subtitle_format': 'srt'},
        })
        service = PipelineService()
        service.run = AsyncMock()
        monkeypatch.setattr(api, 'pipeline_service', service)
        result = await api.decide_compression(task_id, api.CompressionDecision(approve=True))
        accepted = history.get_task(task_id)
        assert result['status'] == 'started'
        assert accepted['pipeline_status'] == 'processing'
        assert accepted['status'] == 'pipeline_starting'
        assert accepted['pipeline_run_id']
        assert accepted['translation_path'] == 'original.json'
        job = service._tasks[task_id]
        await job
        assert service.run.await_args.args[10] is True  # force_dub
        assert service.run.await_args.args[11] is True  # compress_translation
    asyncio.run(scenario())
