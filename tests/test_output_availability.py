import asyncio
from pathlib import Path
from urllib.parse import quote

import pytest

from app.api import tasks
from app.core.config import settings
from app.core.history import HistoryManager


@pytest.fixture
def saved_task(tmp_path, monkeypatch):
    output = tmp_path / 'output'
    output.mkdir()
    monkeypatch.setattr(settings, 'OUTPUT_DIR', output)
    history = HistoryManager(tmp_path / 'history.json')
    task_id = history.create_task('fixture.mp4', 'upload', target_lang='Chinese')
    monkeypatch.setattr(tasks, 'history_manager', history)
    return history, task_id, output


def read_task(task_id, endpoint):
    if endpoint == 'detail':
        return asyncio.run(tasks.get_task_details(task_id))['task']
    return next(task for task in asyncio.run(tasks.list_tasks())['tasks'] if task['task_id'] == task_id)


@pytest.mark.parametrize('endpoint', ['detail', 'list'])
@pytest.mark.parametrize('kind', ['missing', 'empty', 'directory', 'read_error', 'nonempty'])
def test_output_checks_do_not_rewrite_completed_history(saved_task, monkeypatch, endpoint, kind):
    history, task_id, output = saved_task
    paths = [output / name for name in ('subtitle.srt', 'audio.wav', 'video.mp4', 'burn.mp4')]
    for path in paths:
        if kind == 'directory':
            path.mkdir()
        elif kind != 'missing':
            path.write_bytes(b'' if kind == 'empty' else b'fixture')
    history.update_task(task_id, {
        'status': 'completed', 'pipeline_status': 'completed', 'outputs_stale': True,
        'subtitle_outputs': {'srt': str(paths[0])}, 'dubbing_audio_path': str(paths[1]),
        'dubbing_video_path': str(paths[2]), 'burn_path': str(paths[3]),
    })
    before = history.history_file.read_bytes()
    if kind == 'read_error':
        original = Path.open

        def deny_output_read(path, *args, **kwargs):
            if path in paths:
                raise PermissionError('controlled output read failure')
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', deny_output_read)
    result = read_task(task_id, endpoint)
    assert result['output_availability'] == {
        '/static/output/subtitle.srt': kind == 'nonempty',
        '/static/output/audio.wav': kind == 'nonempty',
        '/static/output/video.mp4': kind == 'nonempty',
        '/static/output/burn.mp4': kind == 'nonempty',
    }
    assert result['status'] == result['pipeline_status'] == 'completed'
    assert result['outputs_stale'] is True
    assert result['subtitle_outputs']['srt'] == '/static/output/subtitle.srt'
    assert result['dubbing_video_path'] == '/static/output/video.mp4'
    assert history.history_file.read_bytes() == before


@pytest.mark.parametrize('endpoint', ['detail', 'list'])
def test_refetch_checks_each_file_again_without_invalidating_other_outputs(saved_task, endpoint):
    history, task_id, output = saved_task
    audio, video = output / 'audio.wav', output / 'video.mp4'
    audio.write_bytes(b'audio')
    video.write_bytes(b'video')
    history.update_task(task_id, {'dubbing_audio_path': str(audio), 'dubbing_video_path': str(video)})
    before = history.history_file.read_bytes()
    assert read_task(task_id, endpoint)['output_availability']['/static/output/audio.wav'] is True
    audio.unlink()
    result = read_task(task_id, endpoint)
    assert result['output_availability'] == {'/static/output/audio.wav': False, '/static/output/video.mp4': True}
    audio.write_bytes(b'restored')
    assert read_task(task_id, endpoint)['output_availability']['/static/output/audio.wav'] is True
    assert history.history_file.read_bytes() == before
    assert video.read_bytes() == b'video'


@pytest.mark.parametrize('endpoint', ['detail', 'list'])
def test_local_unicode_and_encoded_url_paths_are_checked(saved_task, endpoint):
    history, task_id, output = saved_task
    (output / '中文 audio.wav').write_bytes(b'audio')
    literal = output / 'literal%20name.mp4'
    literal.write_bytes(b'video')
    url = '/static/output/' + quote('中文 audio.wav')
    history.update_task(task_id, {'dubbing_audio_path': url, 'dubbing_video_path': '/static/output/literal%2520name.mp4',
                                  'burn_path': str(output / '中文 audio.wav')})
    assert read_task(task_id, endpoint)['output_availability'] == {
        url: True, '/static/output/literal%2520name.mp4': True, '/static/output/中文 audio.wav': True,
    }


@pytest.mark.parametrize('endpoint', ['detail', 'list'])
@pytest.mark.parametrize('reference', ['absolute', 'traversal', 'encoded_traversal', 'remote'])
def test_output_availability_never_reads_outside_output_root(saved_task, monkeypatch, endpoint, reference):
    history, task_id, output = saved_task
    outside = output.parent / 'outside.wav'
    outside.write_bytes(b'private fixture')
    value = {'absolute': str(outside), 'traversal': '/static/output/../outside.wav',
             'encoded_traversal': '/static/output/%2e%2e/outside.wav',
             'remote': 'https://example.invalid/audio.wav'}[reference]
    original = Path.open

    def forbid_external_read(path, *args, **kwargs):
        if path.resolve() == outside.resolve():
            pytest.fail('Availability check must not open an external file')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', forbid_external_read)
    history.update_task(task_id, {'dubbing_audio_path': value})
    result = read_task(task_id, endpoint)
    assert result['output_availability'] == {value: False}
    assert result['dubbing_audio_path'] == value


@pytest.mark.parametrize('endpoint', ['detail', 'list'])
def test_no_output_paths_means_empty_availability_not_history_change(saved_task, endpoint):
    history, task_id, _output = saved_task
    before = history.history_file.read_bytes()
    assert read_task(task_id, endpoint)['output_availability'] == {}
    assert history.history_file.read_bytes() == before


@pytest.mark.parametrize('endpoint', ['detail', 'list'])
@pytest.mark.parametrize('name', ['part#1.wav', 'literal%20name.wav'])
def test_local_path_cannot_authorize_a_different_browser_file(saved_task, endpoint, name):
    history, task_id, output = saved_task
    intended = output / name
    intended.write_bytes(b'intended')
    # Both the intended file and the accidental browser target exist.
    (output / 'part').write_bytes(b'wrong target')
    (output / 'literal name.wav').write_bytes(b'wrong target')
    history.update_task(task_id, {'dubbing_audio_path': str(intended)})
    result = read_task(task_id, endpoint)
    assert result['output_availability'] == {'/static/output/' + name: False}
    history.update_task(task_id, {'dubbing_audio_path': '/static/output/' + quote(name)})
    assert read_task(task_id, endpoint)['output_availability'] == {'/static/output/' + quote(name): True}


@pytest.mark.parametrize('endpoint', ['detail', 'list'])
def test_unescaped_url_fragment_is_not_a_readable_download(saved_task, endpoint):
    history, task_id, output = saved_task
    (output / 'part#1.wav').write_bytes(b'intended')
    history.update_task(task_id, {'dubbing_audio_path': '/static/output/part#1.wav'})
    assert read_task(task_id, endpoint)['output_availability'] == {'/static/output/part#1.wav': False}
