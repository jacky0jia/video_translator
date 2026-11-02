"""Bounded recovery for invalid local Whisper decoding, shared by both paths."""
import logging
import math
import re

from app.services.asr_segments import LOCAL_ASR_CONTEXT_SECONDS

logger = logging.getLogger(__name__)


def invalid_segment(segment):
    text = segment.text.strip()
    return (
        not math.isfinite(segment.avg_logprob)
        or bool(re.search(r"(\S)\1{15,}", text))
        or (len(text) >= 32 and not any(char.isalnum() for char in text))
    )


def transcribe_checked(model, audio_path, *, language=None, **options):
    """Retry once with independent deterministic windows; never drop speech."""
    segments, info = model.transcribe(audio_path, language=language, **options)
    segments = list(segments)
    bad = [segment for segment in segments if invalid_segment(segment)]
    if not bad:
        return segments, info
    logger.warning("Invalid ASR decoding at %.2f-%.2fs; retrying once", bad[0].start, bad[0].end)
    retry_options = dict(options)
    retry_options.update(
        temperature=0.0, condition_on_previous_text=False,
        chunk_length=LOCAL_ASR_CONTEXT_SECONDS, repetition_penalty=1.1, no_repeat_ngram_size=6,
    )
    segments, info = model.transcribe(
        audio_path, language=language or info.language, **retry_options
    )
    segments = list(segments)
    bad = [segment for segment in segments if invalid_segment(segment)]
    if bad or not segments:
        span = f"{bad[0].start:.2f}-{bad[0].end:.2f}s" if bad else "unknown time"
        raise RuntimeError(
            f"ASR decoding remains invalid after retry ({span}). "
            "No corrupted transcription was saved. Try CPU float32 or another ASR model."
        )
    return segments, info
