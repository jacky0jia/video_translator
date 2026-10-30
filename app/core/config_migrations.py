"""Versioned, idempotent migrations for the flat YAML configuration."""

from __future__ import annotations

from typing import Any, Mapping


CURRENT_CONFIG_VERSION = 2


def migrate_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    migrated = dict(raw)
    try:
        version = int(migrated.get("CONFIG_VERSION", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("CONFIG_VERSION must be an integer.") from exc
    if version < 0 or version > CURRENT_CONFIG_VERSION:
        raise ValueError(
            f"Unsupported CONFIG_VERSION {version}; this build supports up to {CURRENT_CONFIG_VERSION}."
        )
    if version == 0:
        if str(migrated.get("TTS_MODE", "")).lower().strip() == "local":
            migrated["TTS_MODE"] = "kokoro"
        version = 1
    if version == 1:
        if "SUBTITLE_SEGMENT_SECONDS" not in migrated:
            legacy_duration = migrated.get("ASR_CHUNK_LENGTH", 8)
            duration = legacy_duration if type(legacy_duration) is int else 8
            migrated["SUBTITLE_SEGMENT_SECONDS"] = min(duration, 30) if duration > 0 else 8
        version = 2
    migrated.pop("ASR_CHUNK_LENGTH", None)
    migrated["CONFIG_VERSION"] = version
    return migrated
