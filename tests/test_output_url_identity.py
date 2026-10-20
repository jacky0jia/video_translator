import asyncio
import copy
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from app.api import tasks, translation
from app.core import events
from app.core.config import settings
from app.services.dubbing_service import DubbingService
from app.services.pipeline_service import PipelineService
from app.services.video_burn_service import VideoBurnService


NAMES = [
    ('simple.wav', 'simple.wav'),
    ('two words.wav', 'two%20words.wav'),
    ('中文.wav', '%E4%B8%AD%E6%96%87.wav'),
    ('part#1.wav', 'part%231.wav'),
    ('literal%20name.wav', 'literal%2520name.wav'),
    ('literal%2Fname.wav', 'literal%252Fname.wav'),
]


@pytest.fixture
def output(tmp_path, monkeypatch):
    root = tmp_path / 'output'
    root.mkdir()
    monkeypatch.setattr(settings, 'OUTPUT_DIR', root)
    monkeypatch.setattr(tasks, '_ALLOWED_DELETE_DIRS', (root, tmp_path))
    return root


def publish(entry, file):
    if entry == 'task':
        return tasks._to_url_path(str(file))
    if entry == 'dub':
        return DubbingService._to_download_url(None, file)
    if entry == 'burn':
        return VideoBurnService._to_download_url(None, file)
    return events._browser_event({'dubbing_audio_path': str(file)})['dubbing_audio_path']


def serve(root, url):
    async def request():
        app = Starlette(routes=[Mount('/static/output', app=StaticFiles(directory=root))])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.get(url)
    return asyncio.run(request())


@pytest.mark.parametrize('entry', ['task', 'dub', 'burn', 'SSE'])
@pytest.mark.parametrize('name,encoded', NAMES)
def test_published_url_serves_exact_file_bytes(output, entry, name, encoded):
    file = output / name
    file.write_bytes(b'intended output')
    (output / 'part').write_bytes(b'wrong fragment target')
    (output / 'literal name.wav').write_bytes(b'wrong decoded target')
    url = publish(entry, file)
    assert url == '/static/output/' + encoded
    response = serve(output, url)
    assert response.status_code == 200
    assert response.content == b'intended output'


@pytest.mark.parametrize('consumer', ['translation', 'pipeline'])
@pytest.mark.parametrize('name,encoded', NAMES)
def test_consumers_decode_url_exactly_once_and_keep_absolute_paths(output, consumer, name, encoded):
    file = output / name
    file.write_bytes(b'intended')
    read = translation._resolve_fs_path if consumer == 'translation' else PipelineService._artifact_path
    assert read('/static/output/' + encoded).resolve() == file.resolve()
    assert read(str(file)).resolve() == file.resolve()


@pytest.mark.parametrize('name,encoded', NAMES)
def test_cleanup_deletes_only_exact_url_target(output, name, encoded):
    file = output / name
    file.write_bytes(b'intended')
    keep = output / 'unrelated.wav'
    keep.write_bytes(b'keep')
    accidental = output / 'literal name.wav'
    accidental.write_bytes(b'wrong double-decode target')
    deleted = []
    tasks._delete_output_file('/static/output/' + encoded, deleted)
    assert not file.exists()
    assert [Path(value).resolve() for value in deleted] == [file.resolve()]
    assert keep.read_bytes() == b'keep'
    assert accidental.read_bytes() == b'wrong double-decode target'


@pytest.mark.parametrize('value', [
    '/static/output/part#1.wav', '/static/output/part?1.wav',
    '/static/output/literal%20name.wav',
    '/static/output/../outside.wav', '/static/output/%2e%2e/outside.wav',
])
def test_ambiguous_or_escaping_urls_do_not_resolve_or_delete_files(output, value):
    files = [output / name for name in ('part', 'part#1.wav', 'literal%20name.wav', 'literal name.wav', 'outside.wav')]
    files.append(output.parent / 'outside.wav')
    for file in files:
        file.write_bytes(b'preserve')
    assert translation._resolve_fs_path(value) is None
    assert PipelineService._artifact_path(value) is None
    deleted = []
    tasks._delete_output_file(value, deleted)
    assert deleted == []
    assert all(file.read_bytes() == b'preserve' for file in files)


def test_pipeline_absolute_legacy_path_is_not_reinterpreted_as_url(output):
    file = output.parent / 'legacy%20source.wav'
    file.write_bytes(b'legacy')
    assert PipelineService._artifact_path(str(file)) == file
    assert translation._resolve_fs_path(str(file)) is None


def test_cleanup_does_not_replace_lexical_reference_with_resolved_target(output, monkeypatch):
    source = output / 'source.wav'
    alias = output / 'alias.wav'
    source.write_bytes(b'source must survive')
    alias.write_bytes(b'alias fixture')
    original = Path.resolve

    def resolve_alias(path, *args, **kwargs):
        # Controlled OS resolution boundary also runs on Windows without link privileges.
        return original(source, *args, **kwargs) if path == alias else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', resolve_alias)
    tasks._delete_output_file('/static/output/alias.wav')
    assert source.read_bytes() == b'source must survive'
    assert not alias.exists()


def test_cleanup_removes_real_symlink_not_its_source(output):
    source = output / 'source.wav'
    source.write_bytes(b'source must survive')
    alias = output / 'alias.wav'
    try:
        alias.symlink_to(source)
    except OSError as exc:
        pytest.skip(f'Filesystem symlink creation unavailable: {exc}')
    tasks._delete_output_file('/static/output/alias.wav')
    assert source.read_bytes() == b'source must survive'
    assert not alias.is_symlink()


def test_sse_nested_paths_are_encoded_without_mutating_or_double_encoding(output):
    original = {'transcription_path': str(output / 'part#1.json'),
                'translations': {'Chinese': str(output / 'literal%20name.json')},
                'subtitle_outputs': {'srt': '/static/output/already%23encoded.srt'}}
    before = copy.deepcopy(original)
    result = events._browser_event(original)
    assert result['transcription_path'] == '/static/output/part%231.json'
    assert result['translations'] == {'Chinese': '/static/output/literal%2520name.json'}
    assert result['subtitle_outputs'] == {'srt': '/static/output/already%23encoded.srt'}
    assert original == before


@pytest.mark.parametrize('stem,encoded', [('part#1', 'part%231'), ('literal%20name', 'literal%2520name')])
def test_real_subtitle_export_returns_a_usable_encoded_url(output, stem, encoded):
    source = output / (stem + '.json')
    source.write_text('{"video_source":"fixture.mp4","language":"en","segments":[{"start":0,"end":1,"text":"Hello","confidence":1}]}', encoding='utf-8')
    result = asyncio.run(translation.export_subtitles(
        tasks._to_url_path(str(source)), format='srt', task_id=None, mode='translated', extra_json_paths=None,
        source_color=None, target_color=None, font_size=None, offset_y=None, bold=None, font_family=None,
        download=False, process_route='subtitles', burn_enabled=False,
    ))
    assert result['output_path'] == '/static/output/' + encoded + '.srt'
    assert 'Hello' in serve(output, result['output_path']).text
    assert source.exists()
