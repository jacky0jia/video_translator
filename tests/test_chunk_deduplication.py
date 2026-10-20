import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.chunking_service import deduplicate_segments


def segment(start, end, text, confidence=-0.2):
    return TranscriptionSegment(start=start, end=end, text=text, confidence=confidence)


@pytest.fixture(autouse=True)
def dedup_window(monkeypatch):
    monkeypatch.setattr(settings, "DEDUPLICATE_THRESHOLD_SECONDS", 0.5)


@pytest.mark.parametrize("first,second", [
    (segment(1, 1.2, "First."), segment(1.25, 2, "Different.")),
    (segment(1, 2, "First."), segment(1.25, 3, "Different.")),
    (segment(1, 2, "First."), segment(1, 2, "Different.")),
    (segment(1, 1.25, "Again."), segment(1.25, 2, "Again.")),
    (segment(1, 1.2, "Again."), segment(1.25, 2, "Again.")),
    (segment(1, 2, "Same words."), segment(1.25, 2, "Same words!")),
    (segment(1, 2, " "), segment(1.25, 2, " ")),
    (segment(1, 2, "Again."), segment(1.5, 3, "Again.")),
    (segment(1, 3, "Again."), segment(2, 4, "Again.")),
], ids=["near-disjoint-distinct", "near-overlapping-distinct", "simultaneous-distinct",
        "touching-repeat", "disjoint-repeat", "different-punctuation", "blank",
        "exact-window-boundary", "outside-window"])
def test_uncertain_or_distinct_rows_are_preserved(first, second):
    assert deduplicate_segments([first, second]) == [first, second]


@pytest.mark.parametrize("scores,winner", [((-0.5, -0.1), 1),
                                          ((-0.1, -0.5), 0),
                                          ((-0.2, -0.2), 0)])
def test_confirmed_duplicate_keeps_complete_higher_confidence_row(scores, winner):
    rows = [segment(1, 2, "Same sentence.", scores[0]),
            segment(1.25, 2.5, "Same sentence.", scores[1])]
    rows[winner].source_text_edited = True
    assert deduplicate_segments(rows) == [rows[winner]]


def test_only_outer_whitespace_is_ignored_for_duplicate_text():
    first = segment(1, 2, " Same sentence. ", -0.1)
    second = segment(1.25, 2, "Same sentence.", -0.2)
    assert deduplicate_segments([first, second]) == [first]


@pytest.mark.parametrize("bad_index,bad_end", [(0, float("nan")),
                                               (0, float("inf")),
                                               (1, float("inf"))])
def test_nonfinite_time_cannot_establish_a_duplicate(bad_index, bad_end):
    rows = [segment(1, 2, "Same sentence.", -0.1),
            segment(1.25, 2.5, "Same sentence.", -0.2)]
    rows[bad_index].end = bad_end
    result = deduplicate_segments(rows)
    assert len(result) == 2
    assert result[0] is rows[0]
    assert result[1] is rows[1]


def test_sorting_preserves_all_distinct_rows_without_mutating_input():
    rows = [segment(1.25, 2, "Second."), segment(1, 2, "First.")]
    before = [row.model_dump() for row in rows]
    assert deduplicate_segments(rows) == [rows[1], rows[0]]
    assert [row.model_dump() for row in rows] == before
    assert deduplicate_segments([]) == []
    assert deduplicate_segments([rows[0]]) == [rows[0]]


def test_chunked_transcription_saves_distinct_speech_and_deduplicates_repeat(monkeypatch, tmp_path):
    from app.api import transcribe as api

    audio = tmp_path / "audio.wav"
    audio.touch()
    chunks = [(tmp_path / "chunk-0.wav", 0), (tmp_path / "chunk-1.wav", 57)]
    replies = iter([
        TranscriptionResult(video_source="chunk-0.wav", language="en", segments=[
            segment(57, 58, "Same sentence.", -0.4),
        ]),
        TranscriptionResult(video_source="chunk-1.wav", language="en", segments=[
            segment(0, 1, "Same sentence.", -0.1),
            segment(0.25, 1.5, "Different sentence.", -0.3),
        ]),
    ])

    async def extract(_source):
        return audio

    async def recognize(**_kwargs):
        return next(replies)

    async def cleanup(_path):
        pass

    @asynccontextmanager
    async def stage(_task):
        yield

    updates = []
    monkeypatch.setattr(settings, "CHUNKING_THRESHOLD_MINUTES", 1)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(api.audio_service, "extract_audio", extract)
    monkeypatch.setattr(api.audio_service, "cleanup_audio", cleanup)
    monkeypatch.setattr(api.chunking_service, "get_audio_duration", lambda _path: 100)
    monkeypatch.setattr(api.chunking_service, "split_audio", lambda *_args: chunks)
    monkeypatch.setattr(api.chunking_service, "cleanup_chunks", lambda _paths: None)
    monkeypatch.setattr(api.asr_service, "stage", stage)
    monkeypatch.setattr(api.asr_service, "transcribe", recognize)
    monkeypatch.setattr(api.history_manager, "get_task", lambda _task: None)
    monkeypatch.setattr(api.history_manager, "update_task", lambda _task, update: updates.append(update))
    monkeypatch.setattr(api, "emit_event", lambda *_args: None)

    asyncio.run(api.run_transcription_task("dedup-case", "fixture.mp4", "fixture.mp4", "en"))

    assert updates[-1]["status"] == "transcribed"
    for path_key in ("transcription_path", "transcription_original_path"):
        saved = TranscriptionResult.model_validate_json(
            Path(updates[-1][path_key]).read_text(encoding="utf-8")
        )
        assert [(row.start, row.end, row.text, row.confidence) for row in saved.segments] == [
            (57, 58, "Same sentence.", -0.1),
            (57.25, 58.5, "Different sentence.", -0.3),
        ]
