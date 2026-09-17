"""HTTP adapter for the existing Speaches-compatible API."""

import asyncio
import logging

from app.services.tts.audio import normalized_wav_result
from app.services.tts.base import ProviderHealth, TTSCancelled, TTSCapabilities, TTSSynthesisError, TTSSynthesisRequest, TTSUnavailableError, TTSValidationError, VoiceInfo

logger = logging.getLogger(__name__)


class SpeachesTTSProvider:
    id = "speaches"
    capabilities = TTSCapabilities(supports_voice_clone=True, requires_network=True)

    def __init__(
        self,
        base_url,
        *,
        model,
        client_factory,
        api_key="",
        verify_ssl=True,
        retries=2,
        retry_backoff=3,
        client=None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client_factory = client_factory
        self._api_key = api_key
        self._verify_ssl = verify_ssl
        self._retries = max(int(retries), 1)
        self._retry_backoff = retry_backoff
        self._client = client

    def bind_client(self, client):
        """Use a caller-owned client so one dubbing task reuses its connection pool."""
        self._client = client
        return self

    @property
    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def validate(self, request):
        if not self._base_url:
            raise TTSUnavailableError("TTS_API_URL not configured")
        if not request.text.strip() or not request.voice:
            raise TTSValidationError("Speaches text and voice are required")

    async def synthesize(self, request, should_cancel=None):
        self.validate(request)
        if should_cancel and should_cancel():
            raise TTSCancelled("Speaches synthesis was cancelled")
        payload = {"model": self._model, "input": request.text, "voice": request.voice, "response_format": "wav", "speed": request.speed}
        if request.clone_sample_path:
            payload["voice"] = str(request.clone_sample_path)
        last_error = None
        for attempt in range(self._retries + 1):
            if should_cancel and should_cancel():
                raise TTSCancelled("Speaches synthesis was cancelled")
            try:
                response = await self._post(payload)
                status = getattr(response, "status_code", None)
                if status in {400, 404}:
                    detail = str(getattr(response, "text", ""))[:300]
                    raise TTSValidationError(
                        f"Speaches rejected voice '{request.voice}' ({status}): {detail}"
                    )
                response.raise_for_status()
                break
            except TTSValidationError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self._retries:
                    wait = self._retry_backoff * (attempt + 1)
                    logger.warning(
                        "Speaches attempt %s failed: %s; retrying in %ss",
                        attempt + 1,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
        else:
            raise TTSSynthesisError(
                f"Speaches synthesis failed after retries: {last_error}"
            ) from last_error
        if should_cancel and should_cancel():
            raise TTSCancelled("Speaches synthesis was cancelled")
        return normalized_wav_result(response.content, request.sample_rate)

    async def _post(self, payload):
        if self._client is not None:
            return await self._client.post(
                f"{self._base_url}/v1/audio/speech",
                headers=self._headers,
                json=payload,
                timeout=120.0,
            )
        async with self._client_factory(verify=self._verify_ssl) as client:
            return await client.post(
                f"{self._base_url}/v1/audio/speech",
                headers=self._headers,
                json=payload,
                timeout=120.0,
            )

    async def list_voices(self):
        if not self._base_url:
            return []
        if self._client is not None:
            response = await self._client.get(
                f"{self._base_url}/v1/audio/voices",
                headers=self._headers,
                timeout=10.0,
            )
        else:
            async with self._client_factory(verify=self._verify_ssl) as client:
                response = await client.get(f"{self._base_url}/v1/audio/voices", headers=self._headers, timeout=10.0)
            response.raise_for_status()
        data = response.json()
        voices = data.get("voices", []) if isinstance(data, dict) else data
        return [VoiceInfo(id=str(v.get("id") or v.get("name") or ""), provider=self.id, name=str(v.get("name") or ""), language=str(v.get("language") or ""), gender=str(v.get("gender") or ""), metadata={k: value for k, value in v.items() if k not in {"id", "name", "language", "gender"}}) for v in voices if isinstance(v, dict)]

    async def health(self):
        try:
            await self.list_voices()
            return ProviderHealth(True, "Speaches API is reachable")
        except Exception as exc:
            return ProviderHealth(False, str(exc))
