from typing import List, Optional

from pydantic import BaseModel, Field


class TranscriptionSegment(BaseModel):
    start: float
    end: float
    text: str
    confidence: float


class TranscriptionResult(BaseModel):
    video_source: str
    language: str
    segments: List[TranscriptionSegment]
    alignment_review_rows: List[int] = Field(default_factory=list)
    alignment_check_incomplete: bool = False
