"""Application-owned layout and manifest loader for an offline CosyVoice bundle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.tts.base import TTSUnavailableError


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CosyVoiceBundle:
    root: Path
    worker_config: dict
    voices: tuple[dict[str, str], ...]

    @classmethod
    def load(cls, root: str | Path) -> "CosyVoiceBundle":
        try:
            bundle_root = Path(root).resolve(strict=True)
            value: Any = json.loads(
                (bundle_root / "bundle.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TTSUnavailableError("CosyVoice bundle manifest is unavailable") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise TTSUnavailableError("Unsupported CosyVoice bundle manifest schema")
        if value.get("execution_provider") != "cpu":
            raise TTSUnavailableError("Production CosyVoice bundle must use CPU execution")

        def path_field(name: str, *, directory: bool = False) -> Path:
            raw = value.get(name)
            if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
                raise TTSUnavailableError(f"Invalid CosyVoice bundle path: {name}")
            try:
                resolved = (bundle_root / raw).resolve(strict=True)
                resolved.relative_to(bundle_root)
            except (OSError, ValueError) as exc:
                raise TTSUnavailableError(
                    f"CosyVoice bundle path is unavailable or escapes its root: {name}"
                ) from exc
            if directory != resolved.is_dir():
                raise TTSUnavailableError(f"Invalid CosyVoice bundle path type: {name}")
            return resolved

        runtime_hash = value.get("runtime_script_sha256")
        if not isinstance(runtime_hash, str) or not _SHA256.fullmatch(runtime_hash):
            raise TTSUnavailableError("Invalid CosyVoice runtime script hash")
        raw_presets = value.get("presets")
        if not isinstance(raw_presets, list) or not raw_presets:
            raise TTSUnavailableError("CosyVoice bundle contains no preset voices")
        presets: list[dict] = []
        voices: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_presets:
            if not isinstance(item, dict):
                raise TTSUnavailableError("Invalid CosyVoice preset metadata")
            voice_id = item.get("id")
            language = item.get("language")
            gender = item.get("gender")
            if (
                not isinstance(voice_id, str)
                or not voice_id
                or Path(voice_id).name != voice_id
                or voice_id in seen
                or not isinstance(language, str)
                or not language
                or gender not in {"female", "male"}
            ):
                raise TTSUnavailableError("Invalid CosyVoice preset metadata")
            seen.add(voice_id)
            presets.append(dict(item))
            voices.append(
                {
                    "id": voice_id,
                    "name": str(item.get("name") or voice_id),
                    "language": language,
                    "gender": gender,
                    "source": "bundled_public_domain_reference",
                }
            )

        config = {
            "execution_provider": "cpu",
            "artifact_root": str(path_field("artifact_root", directory=True)),
            "artifact_manifest": str(path_field("artifact_manifest")),
            "runtime_script": str(path_field("runtime_script")),
            "runtime_script_sha256": runtime_hash,
            "model_dir": str(path_field("model_dir", directory=True)),
            "reference_root": str(path_field("reference_root", directory=True)),
            "presets": presets,
        }
        return cls(bundle_root, config, tuple(voices))


def default_cosyvoice_bundle_root(app_dir: Path) -> Path:
    return app_dir.parent / "models" / "cosyvoice"
