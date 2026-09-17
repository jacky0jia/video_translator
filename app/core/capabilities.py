"""Build-time capability manifest exposed by the backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class CapabilityManifest:
    edition: str
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    manifest_version: int = 1

    def to_public_dict(self) -> dict:
        return {
            "manifest_version": self.manifest_version,
            "edition": self.edition,
            "capabilities": dict(self.capabilities),
        }

    def has(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))


STANDARD_CAPABILITIES = CapabilityManifest(
    edition="standard",
    capabilities=MappingProxyType(
        {
            "voice_clone_managed": False,
            "voice_design": False,
            "theme_packages": False,
        }
    ),
)


def get_capability_manifest() -> CapabilityManifest:
    """Factory boundary that a private build may replace without changing public callers."""
    return STANDARD_CAPABILITIES
