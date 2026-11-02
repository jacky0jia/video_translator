import asyncio
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.history import HistoryManager
from app.services import pipeline_service as module
from app.services.pipeline_service import PipelineService


def artifact(root, kind, name):
    path = root / name
    if kind == 'directory':
        path.mkdir()
    elif kind == 'empty':
        path.touch()
    elif kind == 'nonempty':
        path.write_bytes(b'nonempty fixture, not a media decode test')
    return path


@pytest.mark.parametrize('url', [False, True])
@pytest.mark.parametrize('kind,usable', [('missing', False), ('empty', False),
                                       ('directory', False), ('nonempty', True)])
def test_only_regular_nonempty_artifacts_are_usable(tmp_path, monkeypatch, url, kind, usable):
    path = artifact(tmp_path, kind, 'Chinese output.mp4')
    monkeypatch.setattr(settings, 'OUTPUT_DIR', tmp_path)
    value = '/static/output/Chinese%20output.mp4' if url else str(path)
    assert PipelineService._artifact_exists(value) is usable


def test_inaccessible_artifact_is_not_reused(tmp_path, monkeypatch):
    path = artifact(tmp_path, 'nonempty', 'output.mp4')
    original = Path.stat

    def inaccessible(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError('controlled permission failure')
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', inaccessible)
    assert PipelineService._artifact_exists(str(path)) is False


@pytest.fixture
def case(tmp_path, monkeypatch):
    paths = {name: artifact(tmp_path, 'nonempty', name) for name in (
        'source.json', 'translated.json', 'subtitle.srt', 'audio.wav', 'raw.mp4', 'old-final.mp4',
    )}
    history = HistoryManager(tmp_path / 'history.json')
    task_id = history.create_task('fixture.mp4', 'upload', target_lang='Chinese')
    history.update_task(task_id, {
        'status': 'completed', 'transcription_path': str(paths['source.json']),
        'translation_path': str(paths['translated.json']),
        'translations': {'Chinese': str(paths['translated.json'])},
        'subtitle_outputs': {'srt': str(paths['subtitle.srt'])},
        'dubbing_status': 'completed', 'dubbing_target_lang': 'Chinese',
        'dubbing_voice': 'fixture', 'dubbing_speed': 1,
        'dubbing_audio_path': str(paths['audio.wav']),
        'dubbing_raw_video_path': str(paths['raw.mp4']),
        'dubbing_video_path': str(paths['old-final.mp4']),
    })
    events = []
    monkeypatch.setattr(module, 'history_manager', history)
    monkeypatch.setattr(module, 'emit_event', lambda _task, event: events.append(dict(event)))
    # This tests post-review stages, not transcription/translation parsing.
    monkeypatch.setattr(PipelineService, '_review_translation', lambda *_args, **_kwargs: False)
    return history, task_id, paths, events


@pytest.mark.parametrize('field', ['dubbing_audio_path', 'dubbing_raw_video_path'])
@pytest.mark.parametrize('kind', ['missing', 'empty', 'directory'])
def test_invalid_completed_dubbing_is_not_reusable(case, tmp_path, field, kind):
    history, task_id, _paths, _events = case
    history.update_task(task_id, {field: str(artifact(tmp_path, kind, 'invalid'))})
    assert not PipelineService._can_reuse_dubbing(history.get_task(task_id), 'Chinese', 'fixture', 1)


@pytest.mark.parametrize('kind', ['empty', 'directory'])
def test_invalid_cache_triggers_regeneration_without_deleting_old_output(case, tmp_path, kind):
    history, task_id, paths, _events = case
    invalid = artifact(tmp_path, kind, 'invalid-cache')
    history.update_task(task_id, {'dubbing_raw_video_path': str(invalid)})
    calls = []

    async def dub(*_args):
        calls.append('dub')
        history.update_task(task_id, {'dubbing_raw_video_path': str(paths['raw.mp4'])})

    asyncio.run(PipelineService(dub_stage=dub).run(task_id, 'Chinese', 'fixture'))
    assert calls == ['dub']
    assert history.get_task(task_id)['pipeline_status'] == 'completed'
    assert invalid.exists() and paths['old-final.mp4'].is_file()


@pytest.mark.parametrize('stage,field', [
    ('dub', 'dubbing_audio_path'), ('dub', 'dubbing_raw_video_path'),
    ('render', 'dubbing_video_path'), ('render', 'dubbing_audio_path'),
])
@pytest.mark.parametrize('kind', ['missing', 'empty', 'directory'])
def test_invalid_new_output_cannot_publish_pipeline_completion(case, tmp_path, stage, field, kind):
    history, task_id, paths, events = case
    invalid = artifact(tmp_path, kind, 'invalid-new-output')
    originals = {path: path.read_bytes() for path in paths.values()}

    async def dub(*_args):
        if stage == 'dub':
            history.update_task(task_id, {field: str(invalid)})

    async def render(*_args):
        history.update_task(task_id, {field: str(invalid)})

    asyncio.run(PipelineService(dub_stage=dub, burn_stage=render).run(
        task_id, 'Chinese', 'fixture', force_dub=True, burn_subtitles=stage == 'render',
    ))
    saved = history.get_task(task_id)
    assert saved['pipeline_status'] == 'failed'
    assert saved['failed_stage'] == ('pipeline_dubbing' if stage == 'dub' else 'pipeline_rendering')
    assert not any(event.get('status') == 'completed' for event in events)
    assert {path: path.read_bytes() for path in paths.values()} == originals


@pytest.mark.parametrize('render_enabled', [False, True])
def test_regular_nonempty_outputs_still_complete(case, render_enabled):
    history, task_id, paths, events = case

    async def render(*_args):
        history.update_task(task_id, {'dubbing_video_path': str(paths['old-final.mp4'])})

    asyncio.run(PipelineService(burn_stage=render).run(
        task_id, 'Chinese', 'fixture', burn_subtitles=render_enabled,
    ))
    assert history.get_task(task_id)['pipeline_status'] == 'completed'
    assert any(event.get('status') == 'completed' for event in events)
