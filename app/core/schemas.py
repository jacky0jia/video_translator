from typing import List, Optional

from pydantic import BaseModel


class TranscriptionSegment(BaseModel):
    start: float
    end: float
    text: str
    confidence: float


class TranscriptionResult(BaseModel):
    video_source: str
    language: str
    segments: List[TranscriptionSegment]
