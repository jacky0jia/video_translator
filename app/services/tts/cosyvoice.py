"""Optional offline CosyVoice provider backed by the isolated ONNX worker."""

from __future__ import annotations

from typing import Callable, Iterable, Mapping

from app.services.tts.audio import normalized_wav_result
from app.services.tts.base import (
    ProviderHealth,
    TTSCancelled,
    TTSCapabilities,
    TTSSynthesisError,
    TTSSynthesisRequest,
    TTSSynthesisResult,
    TTSUnavailableError,
    TTSValidationError,
    VoiceInfo,
)
from app.workers.cosyvoice_worker import CosyVoiceWorkerClient


class CosyVoiceTTSProvider:
    """Expose reviewed CosyVoice presets without leaking clone implementation details."""

    id = "cosyvoice"
    capabilities = TTSCapabilities(is_local=True, min_speed=1.0, max_speed=1.0)

    def __init__(
        self,
        worker_config_factory: Callable[[], dict],
        voice_source: Callable[[], Iterable[Mapping[str, str]]],
        *,
        client_factory: Callable[[], CosyVoiceWorkerClient] = CosyVoiceWorkerClient,
    ):
        self._worker_config_factory = worker_config_factory
        self._voice_source = voice_source
        self._client_factory = client_factory
        self._client: CosyVoiceWorkerClient | None = None

    def validate(self, request: TTSSynthesisRequest) -> None:
        if not request.text.strip():
            raise TTSValidationError("CosyVoice text cannot be empty")
        if not request.voice or request.voice not in {voice.id for voice in self._voices()}:
            raise TTSValidationError(f"Unknown CosyVoice preset voice: {request.voice}")
        if request.speed != 1.0:
            raise TTSValidationError("CosyVoice generation speed must be 1.0")
        if request.clone_sample_path:
            raise TTSValidationError("CosyVoice accepts application presets only")

    def _voices(self) -> tuple[VoiceInfo, ...]:
        voices = []
        for value in self._voice_source():
            voice_id = str(value.get("id", ""))
            if not voice_id:
                raise TTSUnavailableError("CosyVoice preset has no id")
            voices.append(
                VoiceInfo(
                    id=voice_id,
                    provider=self.id,
                    name=str(value.get("name") or voice_id),
                    language=str(value.get("language", "")),
                    gender=str(value.get("gender", "")),
                    metadata={
                        key: item
                        for key, item in value.items()
                        if key not in {"id", "name", "language", "gender"}
                    },
                )
            )
        if not voices:
            raise TTSUnavailableError("CosyVoice has no configured preset voices")
        return tuple(voices)

    async def _ensure_started(self) -> CosyVoiceWorkerClient:
        if self._client is None or not self._client.running:
            client = self._client_factory()
            await client.start(self._worker_config_factory())
            self._client = client
        return self._client

    async def synthesize(self, request: TTSSynthesisRequest, should_cancel=None) -> TTSSynthesisResult:
        self.validate(request)
        if should_cancel and should_cancel():
            raise TTSCancelled("CosyVoice synthesis was cancelled")
        try:
            client = await self._ensure_started()
            audio = await client.synthesize(
                text=request.text, voice_id=request.voice, should_cancel=should_cancel
            )
            return normalized_wav_result(audio, request.sample_rate)
        except TTSCancelled:
            raise
        except Exception as exc:
            raise TTSSynthesisError(f"CosyVoice synthesis failed: {exc}") from exc

    async def list_voices(self):
        return list(self._voices())

    async def health(self):
        try:
            self._voices()
            config = self._worker_config_factory()
            if not isinstance(config, dict) or not config:
                raise TTSUnavailableError("CosyVoice worker configuration is unavailable")
            return ProviderHealth(True, "CosyVoice CPU bundle is configured")
        except Exception as exc:
            return ProviderHealth(False, str(exc))

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()
