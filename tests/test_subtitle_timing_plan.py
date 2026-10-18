from dataclasses import replace

import pytest

from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.subtitle_timing import apply_timing_plan, plan_subtitle_timing, undo_timing_plan


def track(spans, language='en'):
    return TranscriptionResult(video_source='fixture.mp4', language=language, segments=[
        TranscriptionSegment(start=start, end=end, text=f'Original row {index}', confidence=-0.2)
        for index, (start, end) in enumerate(spans)
    ])


def test_isolated_boundary_plan_applies_and_undoes_all_languages_without_mutation():
    source = track([(0, 2.1), (2, 4), (4, 6)])
    target, japanese = source.model_copy(deep=True), source.model_copy(deep=True)
    target.language, japanese.language = 'Chinese', 'Japanese'
    target.segments[0].text, japanese.segments[0].text = '译文', '翻訳'
    original = [item.model_dump() for item in (source, target, japanese)]
    plan = plan_subtitle_timing(source, [target, japanese])
    assert plan.overlaps == ((1, 2),)
    assert len(plan.changes) == 1 and not plan.issues
    updated = apply_timing_plan(source, [target, japanese], plan)
    assert all(item.segments[0].end == 2 for item in updated)
    assert not plan_subtitle_timing(updated[0], updated[1:]).overlaps
    assert [item.model_dump() for item in (source, target, japanese)] == original
    restored = undo_timing_plan(updated[0], updated[1:], plan)
    assert [item.model_dump() for item in restored] == original
    for before, after in zip((source, target, japanese), updated):
        assert [row.text for row in before.segments] == [row.text for row in after.segments]
        assert len(before.segments) == len(after.segments)


@pytest.mark.parametrize('spans, reason', [
    ([(0, 3), (2, 4)], 'large_overlap'),
    ([(0, 4), (2, 3)], 'nested_overlap'),
    ([(0, 2), (0, 3)], 'same_start'),
    ([(0, 2.1), (2, 4.1), (4, 6)], 'complex_overlap'),
    ([(0, .29), (.19, 2)], 'minimum_duration'),
    ([(0, .35), (.25, 2)], 'large_fraction'),
    ([(0, 2.1), (3, 4), (2, 3)], 'non_adjacent'),
])
def test_ambiguous_overlaps_are_diagnostics_not_destructive_repairs(spans, reason):
    source = track(spans)
    before = source.model_dump()
    plan = plan_subtitle_timing(source)
    assert not plan.changes
    assert reason in [issue.reason for issue in plan.issues]
    assert source.model_dump() == before


@pytest.mark.parametrize('spans', [[], [(0, 2), (2, 4)], [(0, 2), (2.1, 4)], [(0, 2.001), (2, 4)]])
def test_valid_neighbors_and_one_millisecond_tolerance_are_unchanged(spans):
    plan = plan_subtitle_timing(track(spans))
    assert not plan.changes and not plan.overlaps and not plan.issues


@pytest.mark.parametrize('bad', [(0, 0), (2, 1), (-1, 1), (float('nan'), 2), (0, float('inf'))])
def test_invalid_rows_block_even_otherwise_small_repair(bad):
    plan = plan_subtitle_timing(track([(0, 2.1), (2, 4), bad]))
    assert not plan.changes
    assert 'invalid_timing' in [issue.reason for issue in plan.issues]


@pytest.mark.parametrize('mismatch', ['count', 'time'])
def test_unaligned_translation_blocks_track_changes(mismatch):
    source = track([(0, 2.1), (2, 4)])
    target = source.model_copy(deep=True)
    if mismatch == 'count':
        target.segments.pop()
    else:
        target.segments[0].end += .01
    plan = plan_subtitle_timing(source, [target])
    assert not plan.changes
    assert 'translation_mismatch' in [issue.reason for issue in plan.issues]


def test_user_edited_rows_are_not_retimed_by_asr_heuristics():
    source = track([(0, 2.1), (2, 4)])
    source.segments[1].source_text_edited = True
    plan = plan_subtitle_timing(source)
    assert not plan.changes and plan.issues[0].reason == 'source_edited'


@pytest.mark.parametrize('phase', ['before', 'after'])
@pytest.mark.parametrize('edit', ['text', 'timing', 'confidence', 'row_count'])
def test_stale_apply_and_undo_do_not_overwrite_subsequent_edits(phase, edit):
    source = track([(0, 2.1), (2, 4)])
    plan = plan_subtitle_timing(source)
    current = apply_timing_plan(source, [], plan)[0] if phase == 'after' else source
    if edit == 'row_count':
        current.segments.pop()
    elif edit == 'text':
        current.segments[0].text = 'Human correction'
    elif edit == 'confidence':
        current.segments[0].confidence = -1
    else:
        current.segments[0].end += .01
    before = current.model_dump()
    action = undo_timing_plan if phase == 'after' else apply_timing_plan
    with pytest.raises(ValueError):
        action(current, [], plan)
    assert current.model_dump() == before


def test_tampered_plan_cannot_change_arbitrary_rows():
    source = track([(0, 2.1), (2, 4)])
    plan = plan_subtitle_timing(source)
    forged = replace(plan, changes=(replace(plan.changes[0], new_end=.01),))
    with pytest.raises(ValueError):
        apply_timing_plan(source, [], forged)


def test_duplicate_text_is_not_evidence_to_delete_a_row():
    source = track([(0, 3), (2, 4)])
    source.segments[1].text = source.segments[0].text
    plan = plan_subtitle_timing(source)
    updated = apply_timing_plan(source, [], plan)
    assert len(updated[0].segments) == 2
    assert not plan.changes


def test_decoder_window_spill_is_not_assumed_safe_or_clipped_to_eight_seconds():
    source = track([(112, 120.96), (120, 128.24), (128, 134.24)])
    plan = plan_subtitle_timing(source)
    assert plan.overlaps == ((1, 2), (2, 3))
    assert not plan.changes and plan.issues == (plan.issues[0],)
    assert plan.issues[0].reason == 'complex_overlap'
    assert plan.issues[0].rows == (1, 2, 3)


def test_unordered_diagnostic_identifies_local_offending_rows_not_the_whole_track():
    source = track([(0, 1), (1, 2), (4, 5), (3, 4), (6, 7)])
    plan = plan_subtitle_timing(source)
    assert plan.issues[0].reason == 'unordered'
    assert plan.issues[0].rows == (3, 4)
