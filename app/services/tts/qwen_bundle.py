"""Load the local configuration that binds Qwen GGUFs to private llama-tts."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.tts.base import TTSUnavailableError


_HASH = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class QwenTTSBundle:
    root: Path
    worker_config: dict

    @classmethod
    def load(cls, root: str | Path, voice_catalog_root: str | Path) -> "QwenTTSBundle":
        try:
            bundle_root = Path(root).resolve(strict=True)
            value = json.loads((bundle_root / "bundle.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TTSUnavailableError("Qwen3-TTS bundle manifest is unavailable") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise TTSUnavailableError("Unsupported Qwen3-TTS bundle manifest schema")
        if value.get("device") not in {"cuda", "vulkan", "cpu"}:
            raise TTSUnavailableError(
                "Qwen3-TTS device must be cuda or cpu, or vulkan on a supported AMD host"
            )

        def resolve(name: str) -> Path:
            raw = value.get(name)
            if not isinstance(raw, str) or not raw:
                raise TTSUnavailableError(f"Invalid Qwen3-TTS bundle path: {name}")
            try:
                path = Path(raw)
                return (path if path.is_absolute() else bundle_root / path).resolve(strict=True)
            except OSError as exc:
                raise TTSUnavailableError(f"Qwen3-TTS bundle path is unavailable: {name}") from exc

        def digest(name: str) -> str:
            result = value.get(name)
            if not isinstance(result, str) or not _HASH.fullmatch(result):
                raise TTSUnavailableError(f"Invalid Qwen3-TTS hash: {name}")
            return result

        config = {
            "runtime_root": str(resolve("runtime_root")),
            "runtime_manifest": str(resolve("runtime_manifest")),
            "model_path": str(resolve("model_path")),
            "model_sha256": digest("model_sha256"),
            "mmproj_path": str(resolve("mmproj_path")),
            "mmproj_sha256": digest("mmproj_sha256"),
            "voice_catalog_root": str(Path(voice_catalog_root).resolve(strict=True)),
            "device": value["device"],
        }
        return cls(bundle_root, config)


def qwen_worker_config_for_host(
    bundle_config: dict, *, nvidia_available: bool, amd_available: bool,
    auto_cpu_fallback: bool
) -> dict:
    """Select a usable host device without rewriting portable bundle metadata."""
    config = dict(bundle_config)
    if config.get("device") == "cuda" and not nvidia_available:
        config["device"] = "cpu"
    if config.get("device") == "vulkan" and not amd_available:
        config["device"] = "cpu"
    config["auto_cpu_fallback"] = auto_cpu_fallback
    return config


def default_qwen_tts_bundle_root(app_dir: Path) -> Path:
    return app_dir.parent / "models" / "qwen3-tts"
