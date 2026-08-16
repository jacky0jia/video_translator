"""Injectable lifecycle boundary for AI providers.

Phase A deliberately keeps resource scheduling out of this module.  The no-op
default preserves current behaviour; later phases can inject GPU leases,
worker processes, and provider-specific load/unload hooks at this boundary.
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol


class ProviderLifecycle(Protocol):
    async def startup(self, *, task_id: str | None, stage: str, provider: str) -> None:
        """Prepare a provider before a processing stage starts."""

    async def shutdown(self, *, task_id: str | None, stage: str, provider: str) -> None:
        """Release a provider after a processing stage finishes or fails."""


class NoopProviderLifecycle:
    async def startup(self, *, task_id: str | None, stage: str, provider: str) -> None:
        return None

    async def shutdown(self, *, task_id: str | None, stage: str, provider: str) -> None:
        return None


@asynccontextmanager
async def provider_stage(
    lifecycle: ProviderLifecycle,
    *,
    task_id: str | None,
    stage: str,
    provider: str,
) -> AsyncIterator[None]:
    """Run a provider stage with guaranteed lifecycle cleanup."""
    await lifecycle.startup(task_id=task_id, stage=stage, provider=provider)
    try:
        yield
    finally:
        await lifecycle.shutdown(task_id=task_id, stage=stage, provider=provider)
