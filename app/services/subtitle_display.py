"""Stable display-only subtitle blocks, mirrored by subtitleDisplay.js.

Inspired by VLC's placement policy, independently implemented: existing blocks
stay put, new blocks use the lowest free space. No subtitle/audio is retimed.
"""
import math
import re

_SPACE = re.compile(r'[\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+')


def display_tick(seconds):
    return math.floor(seconds * 100 + 1e-7)


def subtitle_display_blocks(tracks):
    groups, invalid = {}, set()
    for track, document in enumerate(tracks):
        for index, row in enumerate(document['segments']):
            start, end, text = row.get('start'), row.get('end'), row.get('text')
            if not isinstance(text, str):
                continue
            text = _SPACE.sub(' ', text).strip(' ')
            if not text:
                continue
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start
                    or end > 90071992547409 or start > 90071992547409):
                invalid.add(index + 1)
                continue
            start_tick = display_tick(start)
            end_tick = max(start_tick + 1, display_tick(end))
            key = f'{index + 1}:{start_tick}:{end_tick}'
            group = groups.setdefault(key, {'id': key, 'row': index + 1, 'start': start_tick, 'end': end_tick, 'parts': []})
            group['parts'].append({'track': track, 'role': document['role'], 'text': text})
    return {'blocks': sorted(groups.values(), key=lambda block: (block['start'], block['row'], block['end'])),
            'invalidRows': sorted(invalid)}


def place_subtitle_blocks(blocks, heights, gap):
    """Allocate against real rendered heights; positions persist for each cue."""
    active, placed = [], []
    for block in blocks:
        height = heights[block['id']]
        if not math.isfinite(height) or height <= 0 or not math.isfinite(gap) or gap < 0:
            raise ValueError('Invalid subtitle block dimensions')
        active = [other for other in active if other['end'] > block['start']]
        bottom = 0
        for other in sorted(active, key=lambda item: item['bottom']):
            if bottom + height + gap <= other['bottom'] + 1e-7:
                break
            if bottom < other['bottom'] + other['height'] + gap:
                bottom = other['bottom'] + other['height'] + gap
        current = {**block, 'bottom': bottom, 'height': height}
        active.append(current)
        placed.append(current)
    return placed


def ass_display_timestamp(tick):
    seconds, centiseconds = divmod(tick, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}'


def escape_ass_text(text):
    # Literal braces must not become positioning/drawing overrides. A zero-width
    # word joiner prevents literal backslashes turning user text into commands.
    return text.replace('\\', '\\\u2060').replace('{', r'\{').replace('}', r'\}')
