"""Qwen3-TTS provider backed by the application-private llama-tts worker."""

from typing import Callable

from app.services.tts.audio import normalized_wav_result
from app.services.tts.base import ProviderHealth, TTSCancelled, TTSCapabilities, TTSSynthesisError, TTSSynthesisRequest, TTSUnavailableError, TTSValidationError
from app.services.tts.qwen_voices import QwenVoiceCatalog
from app.workers.llama_tts_worker import LlamaTTSWorkerClient

_LANGUAGE_ALIASES = {
    "chinese": "zh", "english": "en", "german": "de", "italian": "it",
    "portuguese": "pt", "spanish": "es", "japanese": "ja", "korean": "ko",
    "french": "fr", "russian": "ru",
}

_runtime_status = {"actual_device": None, "state": "idle", "fallback_reason": None}


def get_qwen_runtime_status():
    return dict(_runtime_status)


class QwenTTSProvider:
    id = "qwen"
    capabilities = TTSCapabilities(
        languages=("zh", "en", "de", "it", "pt", "es", "ja", "ko", "fr", "ru"),
        is_local=True, min_speed=1.0, max_speed=1.0,
    )

    def __init__(self, worker_config_factory: Callable[[], dict], *, client_factory=None):
        self._worker_config_factory = worker_config_factory
        self._client_factory = client_factory or (lambda: LlamaTTSWorkerClient(status_callback=_runtime_status.update))
        self._client = None
        self._catalog = None

    def _config(self) -> dict:
        value = self._worker_config_factory()
        if not isinstance(value, dict) or not value:
            raise TTSUnavailableError("Qwen3-TTS worker configuration is unavailable")
        return value

    def _voices(self) -> QwenVoiceCatalog:
        if self._catalog is None:
            self._catalog = QwenVoiceCatalog(self._config()["voice_catalog_root"])
        return self._catalog

    async def list_voices(self):
        return [voice.public_info() for voice in self._voices().list()]

    async def health(self):
        try:
            self._voices()
            return ProviderHealth(True, "Qwen3-TTS private worker is configured")
        except Exception as exc:
            return ProviderHealth(False, str(exc))

    async def synthesize(self, request: TTSSynthesisRequest, should_cancel=None):
        if not request.text.strip():
            raise TTSValidationError("Qwen3-TTS text cannot be empty")
        if request.speed != 1.0:
            raise TTSValidationError("Qwen3-TTS generation speed must be 1.0")
        if request.clone_sample_path:
            raise TTSValidationError("Qwen3-TTS accepts application presets only")
        language = request.language.strip().lower()
        language = _LANGUAGE_ALIASES.get(language, language)
        self._voices().resolve(request.voice, language)
        if should_cancel and should_cancel():
            raise TTSCancelled("Qwen3-TTS synthesis was cancelled")
        try:
            if self._client is None:
                client = self._client_factory()
                try:
                    await client.start(self._config())
                except BaseException:
                    await client.close()
                    raise
                self._client = client
            audio = await self._client.synthesize(
                text=request.text, voice_id=request.voice, language=language,
                should_cancel=should_cancel,
            )
            return normalized_wav_result(audio, request.sample_rate)
        except TTSCancelled:
            raise
        except Exception as exc:
            raise TTSSynthesisError(f"Qwen3-TTS synthesis failed: {exc}") from exc

    async def close(self):
        client, self._client = self._client, None
        if client is not None:
            await client.close()
