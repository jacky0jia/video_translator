"""Stable placement, browser/backend parity and software-only libass regression."""
import copy
import json
import re
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.formatter_service import SubtitleFormatter
from app.services.subtitle_display import subtitle_display_blocks, place_subtitle_blocks, escape_ass_text
from app.services.subtitle_measurement import measure_subtitle_blocks, overlapping_block_ids


def rows(*spans):
    return [dict(start=a, end=b, text=text) for a, b, text in spans]


def document(segments):
    return TranscriptionResult(video_source='fixture.mp4', language='en', segments=[
        TranscriptionSegment(**row, confidence=1) for row in segments])


def test_stable_placement_and_inputs_unchanged():
    tracks = [dict(role='source', segments=rows((0, 3, 'A'), (1, 7, 'B'), (4, 6, 'C'), (7, 8, 'D'))) ]
    before = copy.deepcopy(tracks)
    blocks = subtitle_display_blocks(tracks)['blocks']
    placed = place_subtitle_blocks(blocks, {b['id']: 20 for b in blocks}, 2)
    assert [b['bottom'] for b in placed] == [0, 22, 0, 0]
    assert overlapping_block_ids(blocks) == {b['id'] for b in blocks[:3]}
    assert tracks == before
    with pytest.raises(ValueError):
        place_subtitle_blocks(blocks, {b['id']: 0 for b in blocks}, 2)


def test_python_js_parity():
    # Pure production module, not a reimplementation in the test.
    fixtures = [
        rows((2, 5, 'later'), (0, 4, 'earlier'), (2, 5, 'same'), (5, 6, 'adjacent')),
        rows((0, 1, ' a\n\t b '), (1, 1.001, 'tiny'), (None, 3, 'invalid'), (4, 3, 'reversed'), (0, 1, '\u0085 ')),
        rows((0, 9, 'long ' * 50), (1, 3, '你好'), (2, 8, r'{\pos(0,0)}')),
    ]
    for fixture in fixtures:
        tracks = [dict(role='source', segments=fixture), dict(role='target', segments=fixture[:2])]
        result = subtitle_display_blocks(tracks)
        heights = {b['id']: 10 + index * 17 for index, b in enumerate(result['blocks'])}
        script = "import {buildSubtitleDisplay,placeSubtitleBlocks} from './app/frontend/src/utils/subtitleDisplay.js';let s='';for await(const c of process.stdin)s+=c;const [t,h]=JSON.parse(s),d=buildSubtitleDisplay(t);console.log(JSON.stringify({d,p:placeSubtitleBlocks(d.blocks,h,2)}));"
        output = subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps([tracks, heights]),
            text=True, encoding='utf-8', capture_output=True, check=True, timeout=20)
        assert json.loads(output.stdout) == dict(d=result, p=place_subtitle_blocks(result['blocks'], heights, 2))


def test_formatter_keeps_entire_intervals_and_bilingual_blocks():
    source = document(rows((0, 4, 'Source A'), (2, 6, 'Source B')))
    target = document(rows((0, 4, 'Target A'), (2, 6, 'Target B')))
    with patch('app.services.formatter_service.measure_subtitle_blocks', return_value={'1:0:400': 50, '2:200:600': 75}):
        ass = SubtitleFormatter().to_ass(target, original_result=source, playres_x=640, playres_y=360)
    dialogues = [line for line in ass.splitlines() if line.startswith('Dialogue:')]
    assert len(dialogues) == 2
    assert '0:00:00.00,0:00:04.00' in dialogues[0]
    assert '0:00:02.00,0:00:06.00' in dialogues[1]
    assert r'\pos(320,280)' in dialogues[0]
    assert r'\pos(320,228)' in dialogues[1]
    assert all(line.count(r'\N') == 1 for line in dialogues)
    assert r'\{' in escape_ass_text(r'{\pos(0,0)}')
    assert '\\\u2060pos' in escape_ass_text(r'\pos')


def test_measurement_failure_never_guesses_positions():
    blocks = subtitle_display_blocks([dict(role='source', segments=rows((0, 2, 'A'))) ])['blocks']
    with patch('app.services.subtitle_measurement.subprocess.run', side_effect=subprocess.TimeoutExpired('ffmpeg', 45)):
        with pytest.raises(RuntimeError, match='timed out'):
            measure_subtitle_blocks('PlayResY: 360', blocks, {blocks[0]['id']: 'text'}, 640, 20)
    assert measure_subtitle_blocks('', [], {}, 640, 20) == {}


def test_measurement_uses_unicode_line_breaking_for_unspaced_cjk():
    long_cjk = '这是一段没有空格而且需要自动换行的中文字幕用来验证测量高度' * 4
    block = subtitle_display_blocks([dict(role='target', segments=rows((0, 2, long_cjk)))])['blocks'][0]
    header = SubtitleFormatter().to_ass(document(rows((3, 4, 'header'))), show_source=False,
                                        font_size=20, playres_x=640, playres_y=360).split('Dialogue:')[0]
    heights = measure_subtitle_blocks(header, [block], {block['id']: long_cjk}, 640, 20)
    assert heights[block['id']] > 50


def render_bounds(ass, times):
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, 'fixture.ass').write_text(ass, encoding='utf-8-sig')
        # bbox logs on synthetic black frames. No GPU/model/media input involved.
        result = subprocess.run([settings.ffmpeg_path, '-hide_banner', '-nostdin', '-filter_threads', '1',
            '-f', 'lavfi', '-i', 'color=c=black:s=640x360:r=1', '-vf', 'subtitles=fixture.ass:wrap_unicode=1,bbox=min_val=32',
            '-frames:v', str(max(times) + 1), '-threads', '1', '-f', 'null', '-'], cwd=directory,
            capture_output=True, check=True, timeout=30)
    bounds = {}
    for match in re.finditer(r'n:(\d+)\s+.*?y1:(\d+)\s+y2:(\d+)\s+.*?h:(-?\d+)', result.stderr.decode(errors='replace')):
        index, y1, y2, height = map(int, match.groups())
        if index in times:
            bounds[index] = (y1, y2, height)
    return bounds


def test_real_cpu_libass_new_row_stays_above_after_old_ends():
    long_cjk = '这是一段没有空格而且需要自动换行的中文字幕用来验证重叠布局' * 3
    target = document(rows((0, 3, 'First subtitle'), (1, 6, long_cjk)))
    formatter = SubtitleFormatter()
    ass = formatter.to_ass(target, font_size=20, playres_x=640, playres_y=360)
    dialogues = [line for line in ass.splitlines() if line.startswith('Dialogue:')]
    header = ass.split('Dialogue:')[0]
    combined = render_bounds(ass, [0, 1, 4])
    first = render_bounds(header + dialogues[0], [1])[1]
    second = render_bounds(header + dialogues[1], [1, 4])
    assert first[2] > 0 and second[1][2] > 0
    assert second[1][1] < first[0], 'Measured multiline block collided with earlier subtitle'
    assert combined[1][:2] == (second[1][0], first[1])
    assert second[1] == second[4] == combined[4], 'Later block fell down after earlier subtitle ended'
    # Black text still needs real geometry; measuring user colors against black would miss it.
    dark = formatter.to_ass(target, font_size=20, target_color='#000000', playres_x=640, playres_y=360)
    assert re.findall(r'\\pos\([^)]+\)', dark) == re.findall(r'\\pos\([^)]+\)', ass)
