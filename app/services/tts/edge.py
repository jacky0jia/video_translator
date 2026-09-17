"""Adapter around the existing EdgeTTSService."""

from app.services.tts.audio import normalized_wav_result
from app.services.tts.base import ProviderHealth, TTSCancelled, TTSCapabilities, TTSSynthesisError, TTSSynthesisRequest, TTSValidationError, VoiceInfo
from app.services.tts.registry import tts_language_code


class EdgeTTSProvider:
    id = "edge"
    capabilities = TTSCapabilities(languages=("zh", "en", "ja", "ko"), requires_network=True)

    def __init__(self, service):
        self._service = service

    def validate(self, request: TTSSynthesisRequest) -> None:
        if not request.text.strip() or not request.voice:
            raise TTSValidationError("Edge text and voice are required")
        if request.language and tts_language_code(request.language) not in self.capabilities.languages:
            raise TTSValidationError("Edge supports Chinese, English, Japanese, and Korean")
        if request.clone_sample_path:
            raise TTSValidationError("Edge does not support clone samples")

    async def synthesize(self, request: TTSSynthesisRequest, should_cancel=None):
        self.validate(request)
        if should_cancel and should_cancel():
            raise TTSCancelled("Edge synthesis was cancelled")
        try:
            audio = await self._service.synthesize(request.text, request.voice, request.speed)
        except Exception as exc:
            raise TTSSynthesisError(f"Edge online synthesis failed: {exc}") from exc
        if should_cancel and should_cancel():
            raise TTSCancelled("Edge synthesis was cancelled")
        return normalized_wav_result(audio, request.sample_rate)

    async def list_voices(self):
        voices = await self._service.list_voices()
        return [VoiceInfo(id=v.get("id", v.get("ShortName", "")), provider=self.id, name=v.get("name", v.get("FriendlyName", "")), language=v.get("language", "ko"), gender=v.get("gender", ""), metadata={k: value for k, value in v.items() if k not in {"id", "name", "language", "gender"}}) for v in voices]

    async def health(self):
        try:
            await self._service.list_voices()
            return ProviderHealth(True, "Edge speech service is reachable")
        except Exception as exc:
            return ProviderHealth(False, str(exc))
