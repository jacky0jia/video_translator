"""Measure libass glyph blocks with existing FFmpeg, CPU-only, in one batch."""
import math
import re
import subprocess
import tempfile
from pathlib import Path

from app.core.config import settings
from app.services.subtitle_display import ass_display_timestamp


def overlapping_block_ids(blocks):
    active, result = [], set()
    for block in blocks:
        active = [other for other in active if other['end'] > block['start']]
        if active:
            result.add(block['id'])
            result.update(other['id'] for other in active)
        active.append(block)
    return result


def measure_subtitle_blocks(header, blocks, texts, width, max_font):
    if not blocks:
        return {}
    available_width = width * .9
    # Conservative canvas bound only; actual placement uses measured pixels.
    estimated_lines = max(sum(math.ceil(len(part['text']) * max_font * 1.5 / available_width) + 1
                              for part in block['parts']) for block in blocks)
    height = max(128, math.ceil((estimated_lines + 4) * max_font * 1.5 / 2) * 2)
    if height > 8192:
        raise ValueError('A subtitle block is too large to measure safely; reduce font size or edit the row')
    anchor = height - 8
    header = re.sub(r'(?m)^PlayResY: .*$', f'PlayResY: {height}', header)
    dialogues = [f"Dialogue: 0,{ass_display_timestamp(index * 100)},{ass_display_timestamp((index + 1) * 100)},Translated,,0,0,0,,"
                 + f"{{\\an2\\pos({width // 2},{anchor})}}" + re.sub(r'\\c&H[0-9A-Fa-f]+', lambda _: r'\c&H00FFFFFF', texts[block['id']])
                 for index, block in enumerate(blocks)]
    with tempfile.TemporaryDirectory(prefix='subtitle-layout-') as directory:
        Path(directory, 'measure.ass').write_text(header + '\n' + '\n'.join(dialogues), encoding='utf-8-sig')
        command = [settings.ffmpeg_path, '-hide_banner', '-nostdin', '-filter_threads', '1',
                   '-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:r=1',
                   '-vf', 'ass=measure.ass,bbox=min_val=32', '-frames:v', str(len(blocks)),
                   '-an', '-threads', '1', '-f', 'null', '-']
        try:
            completed = subprocess.run(command, cwd=directory, capture_output=True, timeout=45,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError('Subtitle layout measurement failed or timed out; no new video was produced') from exc
    if completed.returncode:
        raise RuntimeError('Unable to measure subtitle layout with FFmpeg')
    heights = {}
    for match in re.finditer(r'n:(\d+)\s+.*?y1:(\d+)\s+y2:(\d+)\s+.*?h:(-?\d+)', completed.stderr.decode('utf-8', errors='replace')):
        index, y1, _y2, glyph_height = map(int, match.groups())
        if index >= len(blocks):
            continue
        if glyph_height > 0 and y1 == 0:
            raise ValueError('Subtitle measurement canvas overflow; reduce font size or edit the row')
        heights[blocks[index]['id']] = max(1, anchor - y1 + 2) if glyph_height > 0 else 1
    if len(heights) != len(blocks):
        raise RuntimeError('Incomplete subtitle layout measurements; refusing guessed overlapping positions')
    return heights
