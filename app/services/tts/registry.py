"""TTS provider metadata registry and compatibility routing policy."""

from dataclasses import dataclass
from typing import Callable, Iterable

from app.services.tts.base import TTSCapabilities, TTSProvider


EDGE_DEFAULT_VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-JennyNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
}
KOREAN_DEFAULT_VOICE = EDGE_DEFAULT_VOICES["ko"]
_KOREAN_ALIASES = frozenset(
    {"ko", "ko-kr", "korean", "한국어", "韩语", "韓語"}
)
_LANGUAGE_CODES = {
    "chinese": "zh", "zh": "zh", "zh-cn": "zh", "中文": "zh", "汉语": "zh",
    "english": "en", "en": "en", "en-us": "en", "英语": "en", "英文": "en",
    "japanese": "ja", "ja": "ja", "ja-jp": "ja", "jp": "ja", "日语": "ja", "日文": "ja",
    "korean": "ko", "ko": "ko", "ko-kr": "ko", "한국어": "ko", "韩语": "ko", "韓語": "ko",
}


def normalize_language(language: str | None) -> str:
    return str(language or "").strip().lower()


def is_korean_language(language: str | None) -> bool:
    return normalize_language(language) in _KOREAN_ALIASES


def tts_language_code(language: str | None) -> str:
    normalized = normalize_language(language)
    return _LANGUAGE_CODES.get(normalized, normalized.split("-", 1)[0])


def normalize_tts_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower()
    return "kokoro" if normalized == "local" else normalized


@dataclass(frozen=True)
class TTSProviderDescriptor:
    id: str
    display_name: str
    capabilities: TTSCapabilities


@dataclass(frozen=True)
class TTSRoute:
    provider_id: str
    language: str
    default_voice: str | None = None
    reason: str = "configured_provider"


class TTSProviderRegistry:
    """Small immutable-by-convention registry used before adapter activation."""

    def __init__(self, descriptors: Iterable[TTSProviderDescriptor] = ()):
        self._descriptors: dict[str, TTSProviderDescriptor] = {}
        self._factories: dict[str, Callable[[], TTSProvider]] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: TTSProviderDescriptor) -> None:
        provider_id = normalize_tts_mode(descriptor.id)
        if not provider_id:
            raise ValueError("TTS provider id cannot be empty")
        if provider_id in self._descriptors:
            raise ValueError(f"TTS provider already registered: {provider_id}")
        if provider_id != descriptor.id:
            descriptor = TTSProviderDescriptor(
                id=provider_id,
                display_name=descriptor.display_name,
                capabilities=descriptor.capabilities,
            )
        self._descriptors[provider_id] = descriptor

    def get(self, provider_id: str) -> TTSProviderDescriptor:
        normalized = normalize_tts_mode(provider_id)
        try:
            return self._descriptors[normalized]
        except KeyError as exc:
            raise ValueError(f"Unknown TTS provider: {provider_id}") from exc

    def list(self) -> tuple[TTSProviderDescriptor, ...]:
        return tuple(self._descriptors.values())

    def register_provider(
        self,
        provider: TTSProvider | Callable[[], TTSProvider],
        *,
        provider_id: str | None = None,
    ) -> None:
        """Register an injectable adapter without coupling routing to construction."""
        if callable(provider) and provider_id is not None:
            normalized = normalize_tts_mode(provider_id)
            factory = provider
        else:
            instance = provider() if callable(provider) else provider
            normalized = normalize_tts_mode(getattr(instance, "id", ""))
            factory = lambda instance=instance: instance
        self.get(normalized)
        if normalized in self._factories:
            raise ValueError(f"TTS provider factory already registered: {normalized}")
        self._factories[normalized] = factory

    def create(self, provider_id: str) -> TTSProvider:
        normalized = normalize_tts_mode(provider_id)
        self.get(normalized)
        try:
            provider = self._factories[normalized]()
        except KeyError as exc:
            raise ValueError(f"No TTS provider adapter registered: {normalized}") from exc
        if normalize_tts_mode(getattr(provider, "id", "")) != normalized:
            raise ValueError(f"TTS provider adapter id mismatch: {normalized}")
        return provider

    def resolve(self, configured_mode: str, language: str | None) -> TTSRoute:
        normalized_language = normalize_language(language)
        provider_id = normalize_tts_mode(configured_mode)
        self.get(provider_id)
        if provider_id == "edge":
            return TTSRoute(
                provider_id="edge",
                language=normalized_language,
                default_voice=EDGE_DEFAULT_VOICES.get(tts_language_code(normalized_language)),
                reason="configured_provider",
            )
        return TTSRoute(provider_id=provider_id, language=normalized_language)


DEFAULT_TTS_REGISTRY = TTSProviderRegistry(
    (
        TTSProviderDescriptor(
            id="kokoro",
            display_name="Kokoro (local)",
            capabilities=TTSCapabilities(
                is_local=True, min_speed=0.5, max_speed=2.0
            ),
        ),
        TTSProviderDescriptor(
            id="edge",
            display_name="Microsoft Edge TTS",
            capabilities=TTSCapabilities(
                languages=("zh", "en", "ja", "ko"), requires_network=True
            ),
        ),
        TTSProviderDescriptor(
            id="speaches",
            display_name="Speaches API",
            capabilities=TTSCapabilities(
                supports_voice_clone=True, requires_network=True
            ),
        ),
        TTSProviderDescriptor(
            id="qwen",
            display_name="Qwen3-TTS 1.7B (private llama-tts)",
            capabilities=TTSCapabilities(
                languages=("zh", "en", "de", "it", "pt", "es", "ja", "ko", "fr", "ru"),
                is_local=True, min_speed=1.0, max_speed=1.0,
            ),
        ),
        TTSProviderDescriptor(
            id="cosyvoice",
            display_name="CosyVoice (local CPU, optional)",
            capabilities=TTSCapabilities(
                is_local=True, min_speed=1.0, max_speed=1.0
            ),
        ),
    )
)


def resolve_tts_route(configured_mode: str, language: str | None) -> TTSRoute:
    return DEFAULT_TTS_REGISTRY.resolve(configured_mode, language)
