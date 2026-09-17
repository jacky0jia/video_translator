"""TTS provider contracts and routing helpers.

The synthesis adapters are migrated behind these contracts incrementally so
the public dubbing API can remain stable during the refactor.
"""

from app.services.tts.base import (
    ProviderHealth,
    TTSCapabilities,
    TTSProvider,
    TTSSynthesisRequest,
    TTSSynthesisResult,
    TTSCancelled,
    TTSProviderError,
    TTSSynthesisError,
    TTSUnavailableError,
    TTSValidationError,
    VoiceInfo,
)
from app.services.tts.registry import (
    DEFAULT_TTS_REGISTRY,
    KOREAN_DEFAULT_VOICE,
    TTSProviderDescriptor,
    TTSProviderRegistry,
    TTSRoute,
    is_korean_language,
    normalize_tts_mode,
    resolve_tts_route,
)

__all__ = [
    "DEFAULT_TTS_REGISTRY",
    "KOREAN_DEFAULT_VOICE",
    "ProviderHealth",
    "TTSCapabilities",
    "TTSProvider",
    "TTSProviderDescriptor",
    "TTSProviderRegistry",
    "TTSRoute",
    "TTSSynthesisRequest",
    "TTSSynthesisResult",
    "TTSCancelled",
    "TTSProviderError",
    "TTSSynthesisError",
    "TTSUnavailableError",
    "TTSValidationError",
    "VoiceInfo",
    "is_korean_language",
    "normalize_tts_mode",
    "resolve_tts_route",
]
