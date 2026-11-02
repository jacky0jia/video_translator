"""Pure word-timestamp policy for local ASR subtitle rows."""

import math
from typing import Any, Final, Iterable

from app.core.schemas import TranscriptionSegment


LOCAL_ASR_CONTEXT_SECONDS: Final[int] = 30
_PUNCTUATION: Final[str] = ".?!,;:。！？；：，"


def _split_words(segment: Any, limit: float) -> list[TranscriptionSegment] | None:
    """Return None when metadata cannot safely describe the whole segment."""
    try:
        words = list(segment.words)
        if not words:
            return None
        previous_end = segment.start
        for word in words:
            if (
                not math.isfinite(word.start)
                or not math.isfinite(word.end)
                or word.start < previous_end
                or word.end <= word.start
                or word.end > segment.end
                or not isinstance(word.word, str)
            ):
                return None
            previous_end = word.end
    except (AttributeError, TypeError, ValueError):
        return None

    rows = []
    index = 0
    while index < len(words):
        deadline = words[index].start + limit
        last_fitting = index - 1
        last_punctuation = index - 1
        for candidate in range(index, len(words)):
            if words[candidate].end > deadline:
                break
            last_fitting = candidate
            if words[candidate].word.rstrip().endswith(tuple(_PUNCTUATION)):
                last_punctuation = candidate
        # An indivisible word may exceed the limit; always consume it once.
        last = max(index, last_punctuation if last_punctuation >= index else last_fitting)
        text = "".join(word.word for word in words[index:last + 1]).strip()
        if not text:
            return None
        rows.append(TranscriptionSegment(
            start=words[index].start,
            end=words[last].end,
            text=text,
            confidence=segment.avg_logprob,
        ))
        index = last + 1
    return rows


def split_faster_whisper_segments(
    segments: Iterable[Any], max_duration_seconds: float,
) -> list[TranscriptionSegment]:
    """Split each upstream segment independently without estimating word times.

    Short segments and segments with unusable word metadata retain their
    upstream fields. Split rows use word boundaries and inherit confidence.
    """
    if not math.isfinite(max_duration_seconds) or max_duration_seconds <= 0:
        raise ValueError("max_duration_seconds must be finite and positive")

    result = []
    for segment in segments:
        rows = None
        if (
            math.isfinite(segment.start)
            and math.isfinite(segment.end)
            and segment.end - segment.start > max_duration_seconds
        ):
            rows = _split_words(segment, max_duration_seconds)
        if rows is None:
            rows = [TranscriptionSegment(
                start=segment.start,
                end=segment.end,
                text=segment.text,
                confidence=segment.avg_logprob,
            )]
        result.extend(rows)
    return result
