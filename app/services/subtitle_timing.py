"""Pure, reversible timing proposals. Not yet connected to production writes.

Small overlaps are candidates, not proof of erroneous ASR timestamps or silence.
Never delete/reorder rows, merge text, or independently retime a translation.
"""
from dataclasses import dataclass
import hashlib
import json
import math

from app.core.schemas import TranscriptionResult


@dataclass(frozen=True)
class TimingChange:
    index: int  # Original zero-based source row; no sorting of stored rows.
    start: float
    end: float
    new_end: float


@dataclass(frozen=True)
class TimingIssue:
    reason: str
    rows: tuple[int, ...]  # One-based UI row numbers.


@dataclass(frozen=True)
class TimingPlan:
    fingerprint: str
    overlaps: tuple[tuple[int, int], ...]
    changes: tuple[TimingChange, ...]
    issues: tuple[TimingIssue, ...]


def _fingerprint(results):
    # Non-finite timestamps still need a stable diagnostic fingerprint. They
    # are rejected below, never emitted as proposed replacement timestamps.
    payload = json.dumps([result.model_dump(mode='json') for result in results],
                         sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _aligned(results):
    source = results[0].segments
    return all(len(result.segments) == len(source) and all(
        row.start == other.start and row.end == other.end
        for row, other in zip(source, result.segments)
    ) for result in results[1:])


def plan_subtitle_timing(source: TranscriptionResult, translations=()) -> TimingPlan:
    """Classify conflicts and propose only isolated <=120ms boundary trims.

    The previous end is moved to the next start, keeping >=200ms and at least
    90% of the previous span. Edited text, nesting, shared starts, chains and
    unordered input require review. These conservative bounds are provisional,
    not an audio alignment guarantee, and are not exposed as user settings.
    """
    results = (source, *translations)
    segments = source.segments
    issues, changes, pairs = [], [], []
    valid = []
    for index, row in enumerate(segments):
        if not (math.isfinite(row.start) and math.isfinite(row.end) and 0 <= row.start < row.end):
            issues.append(TimingIssue('invalid_timing', (index + 1,)))
        else:
            valid.append(index)
    unordered_rows = {index + 1 for a, b in zip(valid, valid[1:])
                      if segments[b].start < segments[a].start for index in (a, b)}
    unordered = bool(unordered_rows)
    if unordered:
        issues.append(TimingIssue('unordered', tuple(sorted(unordered_rows))))
    aligned = _aligned(results)
    if not aligned:
        issues.append(TimingIssue('translation_mismatch', ()))
    neighbors = {index: set() for index in valid}
    for offset, first in enumerate(valid):
        for second in valid[offset + 1:]:
            a, b = segments[first], segments[second]
            if min(a.end, b.end) - max(a.start, b.start) > 0.001 + 1e-9:
                pairs.append((first + 1, second + 1))
                neighbors[first].add(second)
                neighbors[second].add(first)
    visited = set()
    for index in valid:
        if index in visited or not neighbors[index]:
            continue
        component, pending = set(), [index]
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(neighbors[current] - component)
        visited.update(component)
        ordered = sorted(component)
        rows = tuple(item + 1 for item in ordered)
        if len(ordered) != 2:
            issues.append(TimingIssue('complex_overlap', rows))
            continue
        first, second = ordered
        a, b = segments[first], segments[second]
        if a.start == b.start:
            reason = 'same_start'
        elif b.end <= a.end or b.start < a.start:
            reason = 'nested_overlap'
        elif second != first + 1:
            reason = 'non_adjacent'
        elif a.source_text_edited or b.source_text_edited:
            reason = 'source_edited'
        elif a.end - b.start > 0.12 + 1e-9:
            reason = 'large_overlap'
        elif b.start - a.start < 0.2 - 1e-9:
            reason = 'minimum_duration'
        elif (a.end - b.start) / (a.end - a.start) > 0.1 + 1e-9:
            reason = 'large_fraction'
        elif unordered or not aligned or len(valid) != len(segments):
            reason = 'blocked_by_invalid_track'
        else:
            changes.append(TimingChange(first, a.start, a.end, b.start))
            continue
        issues.append(TimingIssue(reason, rows))
    return TimingPlan(_fingerprint(results), tuple(pairs), tuple(changes), tuple(issues))


def apply_timing_plan(source: TranscriptionResult, translations, plan: TimingPlan):
    """Return independent updated copies; stale/tampered plans cannot apply."""
    results = (source, *translations)
    if plan_subtitle_timing(source, results[1:]) != plan:
        raise ValueError('Timing plan is stale or invalid; analyze the current tracks again')
    copies = tuple(result.model_copy(deep=True) for result in results)
    for change in plan.changes:
        for result in copies:
            result.segments[change.index].end = change.new_end
    return copies


def undo_timing_plan(source: TranscriptionResult, translations, plan: TimingPlan):
    """Undo on copies only if no text/timing/metadata changed since application."""
    results = (source, *translations)
    copies = tuple(result.model_copy(deep=True) for result in results)
    for change in plan.changes:
        for result in copies:
            if change.index >= len(result.segments):
                raise ValueError('Tracks changed after timing application')
            row = result.segments[change.index]
            if row.start != change.start or row.end != change.new_end:
                raise ValueError('Tracks changed after timing application')
            row.end = change.end
    if _fingerprint(copies) != plan.fingerprint or plan_subtitle_timing(copies[0], copies[1:]) != plan:
        raise ValueError('Tracks changed after timing application')
    return copies
