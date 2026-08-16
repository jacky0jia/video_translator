"""Fair, cancellation-safe orchestration for a single shared GPU."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import AsyncIterator, Awaitable, Callable


StatusCallback = Callable[[str, "GPURequest"], Awaitable[None] | None]


@dataclass(frozen=True)
class GPURequest:
    owner: str
    task_id: str | None
    provider: str
    stage: str
    queued_at: float


class GPULease:
    def __init__(self, manager: "GPUManager", request: GPURequest):
        self._manager = manager
        self.request = request
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    async def release(self) -> bool:
        """Release once; repeated calls are harmless and return False."""
        if self._released:
            return False
        self._released = True
        return await self._manager._release(self)

    async def __aenter__(self) -> "GPULease":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.release()


class GPUManager:
    """FIFO single-resource manager that never exposes its internal lock."""

    def __init__(self, status_callback: StatusCallback | None = None):
        self._condition = asyncio.Condition()
        self._queue: list[GPURequest] = []
        self._active: GPULease | None = None
        self._status_callback = status_callback

    async def _notify(self, status: str, request: GPURequest) -> None:
        if not self._status_callback:
            return
        result = self._status_callback(status, request)
        if asyncio.iscoroutine(result):
            await result

    async def acquire(
        self, *, owner: str, task_id: str | None, provider: str, stage: str,
        timeout: float | None = None,
    ) -> GPULease:
        request = GPURequest(owner, task_id, provider, stage, monotonic())
        async with self._condition:
            self._queue.append(request)
            immediate = self._active is None and self._queue[0] is request
        if not immediate:
            await self._notify("waiting_for_gpu", request)

        async def wait_for_turn() -> GPULease:
            async with self._condition:
                await self._condition.wait_for(
                    lambda: self._active is None and self._queue and self._queue[0] is request
                )
                self._queue.pop(0)
                lease = GPULease(self, request)
                self._active = lease
                return lease

        try:
            lease = await asyncio.wait_for(wait_for_turn(), timeout) if timeout is not None else await wait_for_turn()
        except BaseException:
            async with self._condition:
                if request in self._queue:
                    self._queue.remove(request)
                    self._condition.notify_all()
            raise
        await self._notify("switching_model", request)
        return lease

    async def _release(self, lease: GPULease) -> bool:
        async with self._condition:
            if self._active is not lease:
                return False
            self._active = None
            self._condition.notify_all()
            return True

    @asynccontextmanager
    async def lease(self, **metadata) -> AsyncIterator[GPULease]:
        lease = await self.acquire(**metadata)
        try:
            yield lease
        finally:
            await lease.release()

    async def snapshot(self) -> dict:
        async with self._condition:
            return {
                "active": self._active.request if self._active else None,
                "waiting": tuple(self._queue),
            }
