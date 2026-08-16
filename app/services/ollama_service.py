"""Ollama model discovery and explicit residency lifecycle."""
from __future__ import annotations

import logging
from typing import Callable

import httpx

logger = logging.getLogger(__name__)


class OllamaService:
    def __init__(self, base_url: str, *, http_client_factory: Callable = httpx.AsyncClient, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self._http_client_factory = http_client_factory
        self.timeout = timeout

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        async with self._http_client_factory(timeout=self.timeout) as client:
            response = await getattr(client, method)(f"{self.base_url}{path}", **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}

    async def health(self) -> bool:
        await self._request("get", "/api/tags")
        return True

    async def list_models(self) -> list[str]:
        data = await self._request("get", "/api/tags")
        return [item.get("name") or item.get("model") for item in data.get("models", []) if item.get("name") or item.get("model")]

    async def loaded_models(self) -> list[str]:
        data = await self._request("get", "/api/ps")
        return [item.get("name") or item.get("model") for item in data.get("models", []) if item.get("name") or item.get("model")]

    async def is_loaded(self, model: str) -> bool:
        return model in await self.loaded_models()

    async def preload(self, model: str, keep_alive: str = "5m") -> None:
        try:
            await self._request("post", "/api/generate", json={"model": model, "prompt": "", "stream": False, "keep_alive": keep_alive})
        except Exception as exc:
            raise RuntimeError(
                f"Ollama could not load model '{model}'. Confirm that the model is installed: {exc}"
            ) from exc
        if not await self.is_loaded(model):
            raise RuntimeError(f"Ollama model did not become resident after preload: {model}")
        logger.info("Ollama model loaded: %s", model)

    async def unload(self, model: str) -> None:
        try:
            await self._request("post", "/api/generate", json={"model": model, "prompt": "", "stream": False, "keep_alive": 0})
        except Exception as exc:
            raise RuntimeError(f"Ollama failed to unload model '{model}': {exc}") from exc
        if await self.is_loaded(model):
            raise RuntimeError(f"Ollama model is still resident after unload: {model}")
        logger.info("Ollama model unloaded: %s", model)
