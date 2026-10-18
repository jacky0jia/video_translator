import asyncio
from contextlib import asynccontextmanager

import pytest

from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.chunking_service import bound_segments_to_duration


def segment(start, end, text="speech"):
    return TranscriptionSegment(start=start, end=end, text=text, confidence=-0.2)


def test_discards_real_acceptance_padding_hallucination():
    rows = [segment(0, 3), segment(3, 6), segment(6, 10), segment(12, 15, "repeated")]
    result = bound_segments_to_duration(rows, 11.999)
    assert [(row.start, row.end) for row in result] == [(0, 3), (3, 6), (6, 10)]
    assert rows[-1].end == 15


def test_clips_crossing_segment_without_mutating_source():
    source = segment(-0.2, 13, "last sentence")
    result = bound_segments_to_duration([source], 12)
    assert (result[0].start, result[0].end, result[0].text) == (0, 12, "last sentence")
    assert (source.start, source.end) == (-0.2, 13)


def test_discards_padding_sliver_but_keeps_unclipped_short_segment():
    rows = [segment(12, 15, "hallucination"), segment(1, 1.02, "valid")]
    assert [row.text for row in bound_segments_to_duration(rows, 12.028)] == ["valid"]


def test_removes_empty_reversed_and_nonfinite_spans():
    rows = [segment(12, 12), segment(5, 4), segment(float("nan"), 3), segment(1, float("inf"))]
    assert bound_segments_to_duration(rows, 12) == []


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_audio_duration_fails_explicitly(duration):
    with pytest.raises(ValueError):
        bound_segments_to_duration([segment(0, 1)], duration)


def test_transcription_api_bounds_segments_before_saving(monkeypatch, tmp_path):
    from app.api import transcribe as api
    from app.core.config import settings
    audio = tmp_path / "input.wav"
    audio.touch()
    result = TranscriptionResult(video_source=str(audio), language="en",
                                 segments=[segment(0, 13), segment(12, 15)])
    captured = []

    async def extract(_source):
        return audio

    async def recognize(**kwargs):
        return result

    @asynccontextmanager
    async def stage(_task):
        yield

    def save(value, _filename):
        captured.extend(value.segments)
        return tmp_path / "transcription.json"

    monkeypatch.setattr(settings, "CHUNKING_THRESHOLD_MINUTES", 15)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(api.audio_service, "extract_audio", extract)
    monkeypatch.setattr(api.chunking_service, "get_audio_duration", lambda _path: 12.0)
    monkeypatch.setattr(api.asr_service, "stage", stage)
    monkeypatch.setattr(api.asr_service, "transcribe", recognize)
    monkeypatch.setattr(api.asr_service, "save_to_json", save)
    monkeypatch.setattr(api.history_manager, "update_task", lambda *_args: None)
    monkeypatch.setattr(api.history_manager, "get_task", lambda *_args: None)
    monkeypatch.setattr(api, "emit_event", lambda *_args: None)
    asyncio.run(api.run_transcription_task("acceptance", "input.mp4", "input.mp4", "en"))
    assert [(row.start, row.end) for row in captured] == [(0, 12)]
    backups = list(tmp_path.glob('subtitle-version-*.json'))
    assert len(backups) == 1
    archived = TranscriptionResult.model_validate_json(backups[0].read_text(encoding='utf-8'))
    assert [(row.start, row.end) for row in archived.segments] == [(0, 12)]
