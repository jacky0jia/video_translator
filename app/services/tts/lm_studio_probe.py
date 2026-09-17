"""Conservative capability probe for a future LM Studio TTS API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LMStudioTTSProbeResult:
    available: bool
    code: str
    detail: str
    status_code: int | None = None
    media_type: str = ""


class LMStudioTTSProbe:
    """Read-only probe for an explicitly declared LM Studio TTS endpoint.

    LM Studio currently returns HTTP 200 JSON error envelopes for unknown routes,
    so status codes alone are intentionally never treated as capability evidence.
    """

    def __init__(self, base_url: str, *, client_factory, client=None, api_key: str = ""):
        normalized = base_url.rstrip("/")
        self._base_url = normalized[:-3] if normalized.lower().endswith("/v1") else normalized
        self._client_factory = client_factory
        self._client = client
        self._api_key = api_key

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "audio/wav"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def probe(self, model: str) -> LMStudioTTSProbeResult:
        if not self._base_url:
            return LMStudioTTSProbeResult(False, "not_configured", "LM Studio URL is not configured")
        if not model:
            return LMStudioTTSProbeResult(False, "model_not_configured", "Qwen TTS model is not configured")
        try:
            response = await self._options()
        except Exception as exc:
            return LMStudioTTSProbeResult(False, "connection_error", str(exc))
        return self.classify_capability_response(response)

    async def _options(self):
        url = f"{self._base_url}/v1/audio/speech"
        if self._client is not None:
            return await self._client.options(url, headers=self._headers, timeout=10.0)
        async with self._client_factory() as client:
            return await client.options(url, headers=self._headers, timeout=10.0)

    @staticmethod
    def classify_capability_response(response) -> LMStudioTTSProbeResult:
        status = getattr(response, "status_code", None)
        headers = getattr(response, "headers", {}) or {}
        media_type = str(headers.get("content-type", "")).split(";", 1)[0].strip().lower()
        content = bytes(getattr(response, "content", b"") or b"")
        parsed = LMStudioTTSProbe._json_body(response, content)
        if parsed is not None:
            detail = LMStudioTTSProbe._error_detail(parsed)
            code = (
                "unsupported_endpoint"
                if "unexpected endpoint or method" in detail.lower()
                else "json_error_response"
            )
            return LMStudioTTSProbeResult(False, code, detail, status, media_type)
        if status in {404, 405}:
            return LMStudioTTSProbeResult(
                False, "unsupported_endpoint", f"LM Studio TTS endpoint returned HTTP {status}", status, media_type
            )
        allowed = str(headers.get("allow", headers.get("access-control-allow-methods", "")))
        methods = {method.strip().upper() for method in allowed.split(",") if method.strip()}
        if status is not None and 200 <= status < 300 and "POST" in methods:
            return LMStudioTTSProbeResult(
                True,
                "endpoint_declared",
                "LM Studio declares a TTS speech endpoint",
                status,
                media_type,
            )
        return LMStudioTTSProbeResult(
            False,
            "capability_unconfirmed",
            "LM Studio did not declare POST support for the TTS speech endpoint",
            status,
            media_type,
        )

    @staticmethod
    def classify_response(response) -> LMStudioTTSProbeResult:
        status = getattr(response, "status_code", None)
        headers = getattr(response, "headers", {}) or {}
        media_type = str(headers.get("content-type", "")).split(";", 1)[0].strip().lower()
        content = bytes(getattr(response, "content", b"") or b"")

        if media_type.startswith("audio/") and content:
            if media_type in {"audio/wav", "audio/wave", "audio/x-wav"} and not content.startswith(b"RIFF"):
                return LMStudioTTSProbeResult(
                    False, "invalid_audio", "LM Studio returned an invalid WAV body", status, media_type
                )
            return LMStudioTTSProbeResult(
                True, "audio_response", "LM Studio TTS returned audio", status, media_type
            )

        parsed = LMStudioTTSProbe._json_body(response, content)
        if parsed is not None:
            detail = LMStudioTTSProbe._error_detail(parsed)
            lowered = detail.lower()
            code = (
                "unsupported_endpoint"
                if "unexpected endpoint or method" in lowered
                else "json_error_response"
            )
            return LMStudioTTSProbeResult(False, code, detail, status, media_type)

        if status in {404, 405}:
            return LMStudioTTSProbeResult(
                False, "unsupported_endpoint", f"LM Studio TTS endpoint returned HTTP {status}", status, media_type
            )
        return LMStudioTTSProbeResult(
            False,
            "non_audio_response",
            f"LM Studio TTS probe returned non-audio content ({media_type or 'unknown type'})",
            status,
            media_type,
        )

    @staticmethod
    def _json_body(response, content: bytes) -> Any | None:
        try:
            json_method = getattr(response, "json", None)
            if callable(json_method):
                return json_method()
            return json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeError, TypeError):
            return None

    @staticmethod
    def _error_detail(body: Any) -> str:
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                value = error.get("message") or error.get("detail") or error.get("code")
                if value:
                    return str(value)[:500]
            if error:
                return str(error)[:500]
        return "LM Studio returned a JSON response instead of audio"
