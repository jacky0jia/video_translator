"""GPU lease plus LM Studio CLI load/unload lifecycle."""
from __future__ import annotations

import asyncio

from app.core.config import settings
from app.core.gpu_lifecycle import gpu_provider_lifecycle
from app.services.lm_studio_service import LMStudioService


class LMStudioProviderLifecycle:
    def __init__(self, gpu_lifecycle=gpu_provider_lifecycle, service_factory=None):
        self._gpu = gpu_lifecycle
        self._service_factory = service_factory or (
            lambda: LMStudioService(settings.LM_STUDIO_CLI_PATH, settings.LM_STUDIO_PORT)
        )
        self._active = {}

    def _key(self, task_id, stage, provider):
        return (asyncio.current_task(), task_id, stage, provider)

    async def startup(self, *, task_id, stage, provider) -> None:
        await self._gpu.startup(task_id=task_id, stage=stage, provider=provider)
        key = self._key(task_id, stage, provider)
        service = self._service_factory()
        model = settings.LM_STUDIO_MODEL
        try:
            await asyncio.to_thread(service.ensure_server)
            model = await asyncio.to_thread(service.resolve_model, model)
            # Make the auto-selected model visible to the request configuration
            # snapshot created after lifecycle startup.
            settings.LM_STUDIO_MODEL = model
            await asyncio.to_thread(service.load_model, model, settings.LM_STUDIO_TTL_SECONDS)
            self._active[key] = (service, model)
        except BaseException:
            await self._gpu.shutdown(task_id=task_id, stage=stage, provider=provider)
            raise

    async def shutdown(self, *, task_id, stage, provider) -> None:
        active = self._active.pop(self._key(task_id, stage, provider), None)
        try:
            if active:
                service, model = active
                await asyncio.to_thread(service.unload_model, model)
        finally:
            await self._gpu.shutdown(task_id=task_id, stage=stage, provider=provider)


lm_studio_provider_lifecycle = LMStudioProviderLifecycle()
