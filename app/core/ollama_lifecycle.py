"""GPU lease plus Ollama load/unload lifecycle."""
import asyncio

from app.core.config import settings
from app.core.gpu_lifecycle import gpu_provider_lifecycle
from app.services.ollama_service import OllamaService


class OllamaProviderLifecycle:
    def __init__(self, gpu_lifecycle=gpu_provider_lifecycle, service_factory=None):
        self._gpu = gpu_lifecycle
        self._service_factory = service_factory or (lambda base_url: OllamaService(base_url))
        self._active = {}

    def _key(self, task_id, stage, provider):
        return (asyncio.current_task(), task_id, stage, provider)

    async def startup(self, *, task_id, stage, provider) -> None:
        await self._gpu.startup(task_id=task_id, stage=stage, provider=provider)
        key = self._key(task_id, stage, provider)
        model = settings.OLLAMA_MODEL or settings.LLM_MODEL_NAME
        service = self._service_factory(settings.OLLAMA_BASE_URL)
        try:
            await service.health()
            await service.preload(model, settings.OLLAMA_KEEP_ALIVE)
            self._active[key] = (service, model)
        except BaseException:
            await self._gpu.shutdown(task_id=task_id, stage=stage, provider=provider)
            raise

    async def shutdown(self, *, task_id, stage, provider) -> None:
        key = self._key(task_id, stage, provider)
        active = self._active.pop(key, None)
        try:
            if active:
                service, model = active
                await service.unload(model)
        finally:
            await self._gpu.shutdown(task_id=task_id, stage=stage, provider=provider)


ollama_provider_lifecycle = OllamaProviderLifecycle()


class TranslationProviderLifecycle:
    """Select the configured provider for each new translation task."""
    def __init__(self, ollama=ollama_provider_lifecycle, gpu=gpu_provider_lifecycle, lm_studio=None):
        self._ollama = ollama
        self._gpu = gpu
        if lm_studio is None:
            from app.core.lm_studio_lifecycle import lm_studio_provider_lifecycle
            lm_studio = lm_studio_provider_lifecycle
        self._lm_studio = lm_studio
        self._delegates = {}

    def _key(self, task_id, stage, provider):
        return (asyncio.current_task(), task_id, stage, provider)

    async def startup(self, *, task_id, stage, provider) -> None:
        delegate = (
            self._ollama if provider == "ollama"
            else self._lm_studio if provider == "lm_studio"
            else self._gpu
        )
        await delegate.startup(task_id=task_id, stage=stage, provider=provider)
        self._delegates[self._key(task_id, stage, provider)] = delegate

    async def shutdown(self, *, task_id, stage, provider) -> None:
        delegate = self._delegates.pop(self._key(task_id, stage, provider), None)
        if delegate:
            await delegate.shutdown(task_id=task_id, stage=stage, provider=provider)


translation_provider_lifecycle = TranslationProviderLifecycle()
