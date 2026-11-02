"""Real transcription publication/translation/undo with only media/ASR mocked."""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.history import HistoryManager
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.subtitle_timing_versions import SubtitleTimingVersions, TimingVersionConflict


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    from app.api import transcribe, translation
    from app.services.asr_service import ASRService

    history = HistoryManager(tmp_path / 'history.json')
    task_id = history.create_task('fixture.mp4', 'local', target_lang='Chinese')
    result = TranscriptionResult(video_source='fixture.mp4', language='en', segments=[
        TranscriptionSegment(start=0, end=2.1, text='First', confidence=-.2),
        TranscriptionSegment(start=2, end=4, text='Second', confidence=-.2),
        TranscriptionSegment(start=5, end=8, text='Parallel one', confidence=-.2),
        TranscriptionSegment(start=7, end=9, text='Parallel two', confidence=-.2),
    ])

    @asynccontextmanager
    async def stage(_task):
        yield

    monkeypatch.setattr(transcribe, 'history_manager', history)
    monkeypatch.setattr(translation, 'history_manager', history)
    monkeypatch.setattr(transcribe.settings, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(transcribe.settings, 'CHUNKING_THRESHOLD_MINUTES', 15)
    monkeypatch.setattr(transcribe.audio_service, 'extract_audio', AsyncMock(return_value=tmp_path / 'audio.wav'))
    monkeypatch.setattr(transcribe.audio_service, 'cleanup_audio', AsyncMock())
    monkeypatch.setattr(transcribe.chunking_service, 'get_audio_duration', lambda _: 10)
    monkeypatch.setattr(transcribe.asr_service, 'stage', stage)
    monkeypatch.setattr(transcribe.asr_service, 'transcribe', AsyncMock(side_effect=lambda **_: result.model_copy(deep=True)))
    # Keep actual serialization; save_to_json normally writes under settings.OUTPUT_DIR.
    monkeypatch.setattr(transcribe.asr_service, 'save_to_json', ASRService.save_to_json.__get__(transcribe.asr_service))
    monkeypatch.setattr(translation.pipeline_service, 'is_busy', lambda _: False)
    events = []
    monkeypatch.setattr(transcribe, 'emit_event', lambda _id, data: events.append(data))
    monkeypatch.setattr(translation, 'emit_event', lambda *_: None)
    return history, task_id, result, events


def transcribe(fresh):
    from app.api.transcribe import run_transcription_task
    history, task_id, *_ = fresh
    asyncio.run(run_transcription_task(task_id, 'fixture.mp4', 'fixture.mp4', 'en'))
    return history.get_task(task_id)


def document(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def test_new_transcription_repairs_only_small_boundary_after_original_snapshot(fresh):
    state = transcribe(fresh)
    assert state['status'] == 'transcribed'
    assert document(state['transcription_path'])['segments'][0]['end'] == 2
    assert document(state['transcription_original_path'])['segments'][0]['end'] == 2.1
    assert [(s['start'], s['end'], s['text']) for s in document(state['transcription_path'])['segments'][2:]] == [
        (5, 8, 'Parallel one'), (7, 9, 'Parallel two')]
    assert state['subtitle_timing_active']
    assert state['subtitle_timing_summary']['changed_rows'] == [1]
    assert state['subtitle_timing_summary']['remaining_overlaps'] == 1
    assert state['subtitle_timing_summary']['remaining_rows'] == [3, 4]
    assert fresh[3][-1]['subtitle_timing_summary'] == state['subtitle_timing_summary']
    assert fresh[2].segments[0].end == 2.1


def test_automatic_repair_can_be_undone_after_restart_without_reapplying(fresh, tmp_path):
    history, task_id, *_ = fresh
    state = transcribe(fresh)
    restored = SubtitleTimingVersions(HistoryManager(history.history_file), tmp_path).undo(
        task_id, state['state_revision'], state['subtitle_timing_active'])
    assert document(restored['transcription_path'])['segments'][0]['end'] == 2.1
    assert restored['subtitle_timing_summary']['status'] == 'undone'
    assert restored['subtitle_timing_active'] is None
    # Analysis is read-only: refreshing must not silently reapply a user's undo.
    SubtitleTimingVersions(history, tmp_path).analyze(task_id)
    assert document(history.get_task(task_id)['transcription_path'])['segments'][0]['end'] == 2.1


def translate(fresh, monkeypatch):
    from app.api import translation
    history, task_id, *_ = fresh

    async def provider(result, **_):
        copy = result.model_copy(deep=True)
        copy.language = 'Chinese'
        for i, row in enumerate(copy.segments):
            row.text = f'译文 {i + 1}'
        return copy

    monkeypatch.setattr(translation.translation_service, 'translate_full_result', provider)
    state = history.get_task(task_id)
    asyncio.run(translation.translate_transcription(state['transcription_path'], 'Chinese', task_id))
    return history.get_task(task_id)


def test_translation_inherits_repaired_time_and_undo_restores_both_tracks(fresh, tmp_path, monkeypatch):
    history, task_id, *_ = fresh
    transcribe(fresh)
    state = translate(fresh, monkeypatch)
    assert document(state['translation_path'])['segments'][0]['end'] == 2
    assert state['status'] == 'awaiting_review'
    token = state['translation_reviews']['Chinese']['review_id']
    history.update_task(task_id, {'dubbing_audio_path': 'old.wav', 'dubbing_status': 'completed'})
    state = history.get_task(task_id)
    restored = SubtitleTimingVersions(history, tmp_path).undo(task_id, state['state_revision'], state['subtitle_timing_active'])
    for path in (restored['transcription_path'], restored['translation_path']):
        assert document(path)['segments'][0]['end'] == 2.1
    assert document(restored['translation_path'])['segments'][0]['text'] == '译文 1'
    assert restored['translation_reviews']['Chinese']['review_id'] != token
    assert restored['dubbing_status'] == 'stale' and restored['outputs_stale']


@pytest.mark.parametrize('track', ['transcription_path', 'translation_path'])
def test_automatic_undo_rejects_later_text_edits(fresh, tmp_path, monkeypatch, track):
    history, task_id, *_ = fresh
    transcribe(fresh)
    state = translate(fresh, monkeypatch)
    assert state['subtitle_timing_active']
    changed = document(state[track])
    changed['segments'][0]['text'] = 'Human draft'
    Path(state[track]).write_text(json.dumps(changed), encoding='utf-8')
    with pytest.raises(TimingVersionConflict):
        SubtitleTimingVersions(history, tmp_path).undo(task_id, state['state_revision'], state['subtitle_timing_active'])
    assert document(state[track])['segments'][0]['text'] == 'Human draft'


def test_no_small_candidate_does_not_create_repair_version(fresh):
    fresh[2].segments[0].end = 2
    state = transcribe(fresh)
    assert state['status'] == 'transcribed'
    assert not state.get('subtitle_timing_active')
    assert state['subtitle_timing_summary']['changed_rows'] == []
    assert state['subtitle_timing_summary']['remaining_overlaps'] == 1
    assert not state.get('subtitle_timing_versions')


def test_old_timing_undo_is_superseded_by_retranscription(fresh, tmp_path):
    history, task_id, *_ = fresh
    first = transcribe(fresh)
    second = transcribe(fresh)
    assert second['subtitle_timing_active'] != first['subtitle_timing_active']
    assert second['subtitle_timing_versions'][0]['status'] == 'superseded'
    assert len(second['transcription_original_versions']) == 2
    with pytest.raises(TimingVersionConflict):
        SubtitleTimingVersions(history, tmp_path).undo(task_id, second['state_revision'], first['subtitle_timing_active'])


def test_failed_retranscription_publication_does_not_overwrite_previous_active_file(fresh, monkeypatch):
    from app.api import transcribe as api
    state = transcribe(fresh)
    original = Path(state['transcription_path']).read_bytes()
    fresh[2].segments[0].text = 'New recognition'

    def fail(*_):
        raise OSError('Cannot prepare undo metadata')

    monkeypatch.setattr(api, 'record_transcription_timing', fail)
    failed = transcribe(fresh)
    assert failed['status'] == 'failed'
    assert Path(state['transcription_path']).read_bytes() == original


def test_generated_translation_cannot_rebase_undo_over_source_edits(fresh, tmp_path, monkeypatch):
    history, task_id, *_ = fresh
    state = transcribe(fresh)
    changed = document(state['transcription_path'])
    changed['segments'][1]['text'] = 'Human correction'
    Path(state['transcription_path']).write_text(json.dumps(changed), encoding='utf-8')
    state = translate(fresh, monkeypatch)
    with pytest.raises(TimingVersionConflict):
        SubtitleTimingVersions(history, tmp_path).undo(task_id, state['state_revision'], state['subtitle_timing_active'])
    assert document(state['transcription_path'])['segments'][1]['text'] == 'Human correction'


def test_deleting_undone_task_cleans_automatic_active_and_backup_files(fresh, tmp_path, monkeypatch):
    from app.api import tasks
    history, task_id, *_ = fresh
    transcribe(fresh)
    state = translate(fresh, monkeypatch)
    SubtitleTimingVersions(history, tmp_path).undo(task_id, state['state_revision'], state['subtitle_timing_active'])
    monkeypatch.setattr(tasks, 'history_manager', history)
    monkeypatch.setattr(tasks, '_ALLOWED_DELETE_DIRS', (tmp_path,))
    asyncio.run(tasks.delete_task(task_id))
    assert not [path for path in tmp_path.glob('*.json') if path.name != 'history.json']


def test_export_uses_automatic_timing_and_still_requires_translation_review(fresh, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import translation
    from app.services.translation_review_service import TranslationReviewService
    history, task_id, *_ = fresh
    transcribe(fresh)
    state = translate(fresh, monkeypatch)
    app = FastAPI()
    app.include_router(translation.router)
    payload = {'task_id': task_id, 'json_path': state['translation_path'], 'format': 'srt',
               'mode': 'bilingual', 'download': 'false'}
    with TestClient(app) as client:
        assert client.post('/export', data=payload).status_code == 409
        TranslationReviewService(history).approve(task_id, 'Chinese', state['translation_reviews']['Chinese']['review_id'])
        assert client.post('/export', data=payload).status_code == 200
    output = Path(history.get_task(task_id)['subtitle_outputs']['srt']).read_text(encoding='utf-8')
    assert '00:00:00,000 --> 00:00:02,000' in output
    assert 'First' in output and '译文 1' in output


def test_missing_old_translation_does_not_block_regeneration(fresh, monkeypatch):
    transcribe(fresh)
    state = translate(fresh, monkeypatch)
    Path(state['translation_path']).unlink()
    regenerated = translate(fresh, monkeypatch)
    assert regenerated['status'] == 'awaiting_review'
    assert document(regenerated['translation_path'])['segments'][0]['text'] == '译文 1'


def test_existing_translation_binding_prevents_source_only_automatic_retiming(fresh, tmp_path):
    history, task_id, result, _events = fresh
    target = tmp_path / 'previous-translation.json'
    target.write_text(result.model_dump_json(), encoding='utf-8')
    original = target.read_bytes()
    history.update_task(task_id, {'translation_path': str(target), 'translations': {'Chinese': str(target)}})
    state = transcribe(fresh)
    assert document(state['transcription_path'])['segments'][0]['end'] == 2.1
    assert target.read_bytes() == original
    assert not state['subtitle_timing_active']
    assert state['subtitle_timing_summary']['remaining_overlaps'] == 2
