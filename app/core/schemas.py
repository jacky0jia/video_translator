import math
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class TranscriptionSegment(BaseModel):
    start: float
    end: float
    text: str
    confidence: float

    @field_validator("confidence", mode="before")
    @classmethod
    def finite_confidence(cls, value):
        # Older ASR output serialized NaN/Infinity as null. Unknown is not
        # perfect confidence; use a finite log-probability sentinel instead.
        if value is None:
            return -1.0
        numeric = float(value)
        return numeric if math.isfinite(numeric) else -1.0


class TranscriptionResult(BaseModel):
    video_source: str
    language: str
    segments: List[TranscriptionSegment]
    alignment_review_rows: List[int] = Field(default_factory=list)
    alignment_check_incomplete: bool = False
