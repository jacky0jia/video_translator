"""Provider-neutral TTS domain contracts."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable


class TTSProviderError(RuntimeError):
    """Base error safe to surface at the provider boundary."""


class TTSValidationError(TTSProviderError):
    """The request cannot be handled by the selected provider."""


class TTSUnavailableError(TTSProviderError):
    """The provider or one of its required resources is unavailable."""


class TTSSynthesisError(TTSProviderError):
    """The provider failed to return usable audio."""


class TTSCancelled(TTSProviderError):
    """Synthesis was cancelled by the caller."""


@dataclass(frozen=True)
class TTSCapabilities:
    languages: tuple[str, ...] = ()
    supports_voice_clone: bool = False
    supports_voice_design: bool = False
    is_local: bool = False
    requires_network: bool = False
    min_speed: float = 0.25
    max_speed: float = 4.0


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    provider: str = ""
    language: str = ""
    gender: str = ""
    name: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TTSSynthesisRequest:
    text: str
    voice: str
    speed: float = 1.0
    language: str = ""
    sample_rate: int = 22050
    clone_sample_path: Path | None = None


@dataclass(frozen=True)
class TTSSynthesisResult:
    audio: bytes
    media_type: str = "audio/wav"
    sample_rate: int | None = None
    channels: int = 1
    duration_seconds: float | None = None


@dataclass(frozen=True)
class ProviderHealth:
    available: bool
    detail: str = ""


@runtime_checkable
class TTSProvider(Protocol):
    """Contract implemented by current and future TTS adapters."""

    id: str
    capabilities: TTSCapabilities

    async def synthesize(
        self,
        request: TTSSynthesisRequest,
        should_cancel: Callable[[], bool] | None = None,
    ) -> TTSSynthesisResult:
        ...

    async def list_voices(self) -> Sequence[VoiceInfo]:
        ...

    async def health(self) -> ProviderHealth:
        ...
