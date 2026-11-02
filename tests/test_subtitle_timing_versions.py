import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.history import HistoryManager
from app.services.subtitle_timing_versions import SubtitleTimingVersions, TimingVersionConflict, subtitle_version_paths
from app.services.translation_review_service import TranslationReviewService


@pytest.fixture
def case(tmp_path, monkeypatch):
    import app.api.tasks as api
    history = HistoryManager(tmp_path / 'history.json')
    task_id = history.create_task('fixture.mp4', 'local', target_lang='Chinese')
    paths = {}
    for language in ('source', 'Chinese', 'Japanese'):
        path = tmp_path / f'{language}.json'
        document = {'video_source': 'fixture.mp4', 'language': language, 'custom_metadata': {'keep': 1}, 'segments': [
            {'start': 0, 'end': 2.1, 'text': f'{language} one', 'confidence': None, 'speaker': 'A'},
            {'start': 2, 'end': 4, 'text': f'{language} two', 'confidence': -0.2},
        ]}
        path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        paths[language] = str(path)
    history.update_task(task_id, {'status': 'completed', 'transcription_path': paths['source'],
        'translation_path': paths['Chinese'], 'translations': {key: paths[key] for key in ('Chinese', 'Japanese')},
        'transcription_duration': 4, 'dubbing_status': 'completed', 'dubbing_audio_path': 'old.wav',
        'dubbing_video_path': 'old.mp4', 'burn_path': 'old-burn.mp4', 'burn_status': 'completed',
        'subtitle_outputs': {'srt': 'old.srt'}, 'compression_attempts': 2,
        'dubbing_review': {'status': 'required', 'rows': [1]}})
    review = TranslationReviewService(history)
    for language in ('Chinese', 'Japanese'):
        token = review.request(task_id, language)
        review.approve(task_id, language, token['review_id'])
    monkeypatch.setattr(api, 'history_manager', history)
    monkeypatch.setattr(api.settings, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(api.pipeline_service, 'is_busy', Mock(return_value=False))
    monkeypatch.setattr(api, 'emit_event', Mock())
    monkeypatch.setattr(api, '_ALLOWED_DELETE_DIRS', (tmp_path,))
    service = SubtitleTimingVersions(history, tmp_path)
    return service, history, task_id, paths


def apply(case):
    service, _history, task_id, _paths = case
    plan = service.analyze(task_id)
    return service.apply(task_id, plan['state_revision'], plan['fingerprint'])


def test_atomic_pointer_switch_retains_bytes_metadata_and_invalidates_outputs_and_reviews(case):
    service, history, task_id, paths = case
    originals = {key: Path(value).read_bytes() for key, value in paths.items()}
    before = history.get_task(task_id)
    plan = service.analyze(task_id)
    assert plan['changes'][0]['row'] == 1
    assert not list(service.root.glob('subtitle-version-*.json'))
    state = apply(case)
    assert state['state_revision'] == before['state_revision'] + 1
    assert state['translation_path'] == state['translations']['Chinese']
    assert state['status'] == state['pipeline_status'] == 'awaiting_review'
    assert state['subtitle_outputs'] == {} and state['outputs_stale']
    assert state['dubbing_status'] == state['burn_status'] == 'stale'
    assert state['compression_attempts'] == 2 and state['dubbing_review']['needs_recheck']
    for language in ('Chinese', 'Japanese'):
        assert state['translation_reviews'][language]['status'] == 'pending'
        assert state['translation_reviews'][language]['review_id'] != before['translation_reviews'][language]['review_id']
        assert not TranslationReviewService(history).is_approved(task_id, language)
    for path in {state['transcription_path'], *state['translations'].values()}:
        document = json.loads(Path(path).read_bytes())
        assert document['segments'][0]['end'] == 2
        assert document['segments'][0]['speaker'] == 'A'
        assert document['segments'][0]['confidence'] is None
        assert document['custom_metadata'] == {'keep': 1}
    assert {key: Path(value).read_bytes() for key, value in paths.items()} == originals
    assert len(subtitle_version_paths(state)) == 6
    # Reload the history/service, proving undo does not depend on in-memory state.
    restored = SubtitleTimingVersions(HistoryManager(history.history_file), service.root).undo(
        task_id, state['state_revision'], state['subtitle_timing_active'])
    assert restored['subtitle_timing_active'] is None and restored['subtitle_timing_revision'] == 2
    assert Path(restored['transcription_path']).read_bytes() == originals['source']
    for language in ('Chinese', 'Japanese'):
        assert Path(restored['translations'][language]).read_bytes() == originals[language]
    assert restored['status'] == 'awaiting_review' and restored['outputs_stale']
    assert len(subtitle_version_paths(restored)) == 9


@pytest.mark.parametrize('change', ['revision', 'text', 'metadata', 'path'])
def test_stale_analysis_cannot_publish(case, change):
    service, history, task_id, paths = case
    plan = service.analyze(task_id)
    if change == 'revision':
        history.update_task(task_id, {'message': 'new state'})
    elif change == 'path':
        duplicate = service.root / 'replacement.json'
        duplicate.write_bytes(Path(paths['Chinese']).read_bytes())
        history.update_task(task_id, {'translation_path': str(duplicate)})
    else:
        path = Path(paths['Japanese'])
        document = json.loads(path.read_bytes())
        if change == 'text':
            document['segments'][0]['text'] = 'Human edit'
        else:
            document['custom_metadata']['keep'] = 2
        path.write_text(json.dumps(document), encoding='utf-8')
    before = history.get_task(task_id)
    with pytest.raises(TimingVersionConflict):
        service.apply(task_id, plan['state_revision'], plan['fingerprint'])
    assert history.get_task(task_id) == before
    assert not list(service.root.glob('subtitle-version-*.json'))


@pytest.mark.parametrize('failure', ['write', 'cas', 'concurrent_history', 'concurrent_file'])
def test_staging_failures_leave_old_complete_track_set_active(case, monkeypatch, failure):
    import app.services.subtitle_timing_versions as module
    service, history, task_id, paths = case
    before = history.get_task(task_id)
    real_write = module.write_subtitle_snapshot
    calls = []
    def write(root, data):
        calls.append(1)
        if len(calls) == 3:
            if failure == 'write':
                raise OSError('disk failure')
            if failure == 'concurrent_history':
                history.update_task(task_id, {'message': 'new external state'})
            if failure == 'concurrent_file':
                Path(paths['Japanese']).write_text(Path(paths['Japanese']).read_text() + ' ', encoding='utf-8')
        return real_write(root, data)
    monkeypatch.setattr(module, 'write_subtitle_snapshot', write)
    if failure == 'cas':
        monkeypatch.setattr(history, 'compare_and_update_task', Mock(return_value=False))
    with pytest.raises((OSError, TimingVersionConflict)):
        apply(case)
    state = history.get_task(task_id)
    for key in ('transcription_path', 'translation_path', 'translations', 'subtitle_outputs', 'translation_reviews'):
        assert state[key] == before[key]
    assert not state.get('subtitle_timing_active')


@pytest.mark.parametrize('edit', ['source_text', 'target_text', 'timing', 'metadata', 'backup', 'language_added'])
def test_undo_refuses_to_overwrite_edits_or_corrupt_backups(case, edit):
    service, history, task_id, _paths = case
    state = apply(case)
    path = Path(state['transcription_path'] if edit == 'source_text' else state['translations']['Japanese'])
    if edit == 'backup':
        path = Path(state['subtitle_timing_versions'][-1]['before']['source'])
    if edit == 'language_added':
        history.update_task(task_id, {'translations': {**state['translations'], 'French': state['translations']['Japanese']}})
        state = history.get_task(task_id)
    else:
        document = json.loads(path.read_bytes())
        if edit in ('source_text', 'target_text', 'backup'):
            document['segments'][0]['text'] = 'human edit'
        elif edit == 'timing':
            document['segments'][0]['end'] -= .01
        else:
            document['custom_metadata']['keep'] = 3
        path.write_text(json.dumps(document), encoding='utf-8')
    before = history.get_task(task_id)
    with pytest.raises(TimingVersionConflict):
        service.undo(task_id, state['state_revision'], state['subtitle_timing_active'])
    assert history.get_task(task_id) == before


@pytest.mark.parametrize('busy', [
    {'pipeline_status': 'processing'}, {'pipeline_status': 'cancelling'},
    {'pipeline_status': 'awaiting_compression'}, {'pipeline_cleanup_pending': True},
    {'dubbing_status': 'processing'}, {'burn_status': 'processing'}, {'status': 'transcribing'},
])
def test_processing_and_cleanup_block_all_operations(case, busy):
    service, history, task_id, _paths = case
    history.update_task(task_id, busy)
    with pytest.raises(TimingVersionConflict):
        service.analyze(task_id)
    with pytest.raises(TimingVersionConflict):
        service.apply(task_id, 0, 'x')
    with pytest.raises(TimingVersionConflict):
        service.undo(task_id, 0, 'x')


@pytest.mark.parametrize('invalid', ['missing', 'outside', 'mismatch', 'beyond_duration'])
def test_invalid_tracks_fail_without_modifying_history(case, tmp_path, invalid):
    service, history, task_id, paths = case
    if invalid in ('missing', 'outside'):
        history.update_task(task_id, {'transcription_path': str(tmp_path.parent / 'outside.json')})
    else:
        path = Path(paths['Japanese'])
        document = json.loads(path.read_bytes())
        document['segments'][0]['end'] = 8 if invalid == 'beyond_duration' else 2.11
        path.write_text(json.dumps(document), encoding='utf-8')
    before = history.get_task(task_id)
    if invalid == 'mismatch':
        plan = service.analyze(task_id)
        assert not plan['changes']
        assert service.apply(task_id, plan['state_revision'], plan['fingerprint']) == before
    else:
        with pytest.raises(ValueError):
            service.analyze(task_id)
    assert history.get_task(task_id) == before


def test_live_worker_and_reserved_translation_are_busy(case, monkeypatch):
    import app.services.subtitle_timing_versions as module
    service, history, task_id, _paths = case
    service.busy = lambda _: True
    with pytest.raises(TimingVersionConflict):
        service.analyze(task_id)
    service.busy = lambda _: False
    monkeypatch.setattr(module, 'translation_busy', lambda _: True)
    with pytest.raises(TimingVersionConflict):
        service.analyze(task_id)


def test_http_analyze_apply_undo_and_stale_duplicate_are_version_guarded(case):
    import app.api.tasks as api
    service, history, task_id, _paths = case
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        plan = client.get(f'/tasks/{task_id}/subtitle-timing').json()
        payload = {key: plan[key] for key in ('state_revision', 'fingerprint')}
        response = client.post(f'/tasks/{task_id}/subtitle-timing/apply', json=payload)
        assert response.status_code == 200 and response.json()['changed']
        assert client.post(f'/tasks/{task_id}/subtitle-timing/apply', json=payload).status_code == 409
        active = response.json()
        response = client.post(f'/tasks/{task_id}/subtitle-timing/undo', json={
            'state_revision': active['state_revision'], 'version_id': active['active_version']})
        assert response.status_code == 200 and response.json()['active_version'] is None
        assert client.get('/tasks/missing/subtitle-timing').status_code == 404
        assert client.post(f'/tasks/{task_id}/subtitle-timing/apply', json={'state_revision': -1, 'fingerprint': 'x'}).status_code == 422
    assert api.emit_event.call_count == 2


def test_new_repair_after_human_edits_keeps_those_edits_when_undoing_latest(case):
    service, history, task_id, _paths = case
    state = apply(case)
    for path in {state['transcription_path'], *state['translations'].values()}:
        document = json.loads(Path(path).read_bytes())
        document['segments'][0]['end'] = 2.05
        document['segments'][1]['text'] += ' human edit'
        Path(path).write_text(json.dumps(document), encoding='utf-8')
    before = {path: Path(path).read_bytes() for path in {state['transcription_path'], *state['translations'].values()}}
    second = apply(case)
    assert second['subtitle_timing_versions'][0]['status'] == 'superseded'
    restored = service.undo(task_id, second['state_revision'], second['subtitle_timing_active'])
    assert Path(restored['transcription_path']).read_bytes() == before[state['transcription_path']]
    assert restored['subtitle_timing_active'] is None


def test_export_after_review_consumes_current_tracks_not_stale_client_path(case, monkeypatch):
    import app.api.translation as translation
    service, history, task_id, paths = case
    state = apply(case)
    monkeypatch.setattr(translation, 'history_manager', history)
    monkeypatch.setattr(translation.pipeline_service, 'is_busy', Mock(return_value=False))
    app = FastAPI()
    app.include_router(translation.router)
    payload = {'task_id': task_id, 'json_path': paths['Chinese'], 'format': 'srt', 'mode': 'bilingual', 'download': 'false'}
    with TestClient(app) as client:
        assert client.post('/export', data=payload).status_code == 409
        review = state['translation_reviews']['Chinese']
        TranslationReviewService(history).approve(task_id, 'Chinese', review['review_id'])
        response = client.post('/export', data=payload)
        assert response.status_code == 200
    exported = Path(history.get_task(task_id)['subtitle_outputs']['srt']).read_text(encoding='utf-8')
    assert '00:00:00,000 --> 00:00:02,000' in exported
    assert 'source one' in exported and 'Chinese one' in exported


def test_completed_dubbing_cannot_be_reused_after_timing_version_change(case):
    from app.services.pipeline_service import PipelineService
    state = apply(case)
    assert not PipelineService._can_reuse_dubbing(state, 'Chinese', 'voice', 1)


def test_legacy_missing_selected_path_is_restored_from_current_language(case):
    _service, history, task_id, _paths = case
    history.update_task(task_id, {'translation_path': None})
    state = apply(case)
    assert state['translation_path'] == state['translations']['Chinese']
    assert state['translation_reviews']['Chinese']['status'] == 'pending'


def test_source_only_repair_does_not_create_or_approve_translation(case):
    _service, history, task_id, _paths = case
    history.update_task(task_id, {'translation_path': None, 'translations': {}, 'translation_reviews': {}})
    state = apply(case)
    assert state['status'] == 'transcribed' and state['pipeline_status'] is None
    assert not state['translations'] and not state['translation_reviews']


@pytest.mark.parametrize('mode', ['single', 'batch', 'all'])
def test_existing_task_deletion_removes_managed_versions_but_not_unrelated_files(case, mode):
    import app.api.tasks as api
    _service, history, task_id, _paths = case
    state = apply(case)
    managed = subtitle_version_paths(state)
    unrelated = Path(state['transcription_path']).parent / 'unrelated.json'
    unrelated.write_text('{}', encoding='utf-8')
    if mode == 'single':
        asyncio.run(api.delete_task(task_id))
    elif mode == 'batch':
        asyncio.run(api.batch_delete_tasks(api.BatchDeleteRequest(task_ids=[task_id])))
    else:
        asyncio.run(api.delete_all_tasks())
    assert all(not Path(path).exists() for path in managed)
    assert unrelated.exists()
