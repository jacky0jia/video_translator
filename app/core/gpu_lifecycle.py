"""Provider lifecycle adapter backed by the project-wide GPU manager."""
import asyncio

from app.core.events import emit_event
from app.core.gpu_manager import GPUManager, GPURequest
from app.core.history import history_manager


def _publish_gpu_status(status: str, request: GPURequest) -> None:
    if not request.task_id:
        return
    data = {
        "status": status,
        "message": "等待 GPU 资源..." if status == "waiting_for_gpu" else "正在切换 AI 模型...",
        "gpu_stage": request.stage,
        "gpu_provider": request.provider,
    }
    history_manager.update_task(request.task_id, data)
    emit_event(request.task_id, data)


gpu_manager = GPUManager(status_callback=_publish_gpu_status)


class GPUProviderLifecycle:
    def __init__(self, manager: GPUManager = gpu_manager, *, owner: str = "application"):
        self._manager = manager
        self._owner = owner
        self._leases = {}

    def _key(self, task_id, stage, provider):
        return (asyncio.current_task(), task_id, stage, provider)

    async def startup(self, *, task_id, stage, provider) -> None:
        key = self._key(task_id, stage, provider)
        if key in self._leases:
            raise RuntimeError(f"GPU stage already started: {stage}/{provider}")
        self._leases[key] = await self._manager.acquire(
            owner=self._owner, task_id=task_id, provider=provider, stage=stage
        )

    async def shutdown(self, *, task_id, stage, provider) -> None:
        lease = self._leases.pop(self._key(task_id, stage, provider), None)
        if lease:
            await lease.release()


gpu_provider_lifecycle = GPUProviderLifecycle()
