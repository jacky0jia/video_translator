"""Validate tracked and optional external Qwen3-TTS release inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReleaseAssetError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _direct_filename(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise ReleaseAssetError(f"Invalid {label} filename")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ReleaseAssetError(f"Invalid {label} SHA-256")
    return value


def validate_release_manifest(
    manifest_path: str | Path,
    *,
    archives_dir: str | Path | None = None,
    model_dir: str | Path | None = None,
) -> dict:
    path = Path(manifest_path).resolve(strict=True)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseAssetError("Release manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ReleaseAssetError("Unsupported release manifest schema")
    if manifest.get("package_revision") != 3 or manifest.get("platform") != "windows-x86_64":
        raise ReleaseAssetError("Unsupported release package")
    if not isinstance(manifest.get("runtime_version"), str):
        raise ReleaseAssetError("Release runtime version is missing")

    support = manifest.get("support_files")
    if not isinstance(support, dict) or not support:
        raise ReleaseAssetError("Release support files are missing")
    for filename, expected in support.items():
        name = _direct_filename(filename, "support file")
        if sha256(path.parent / name) != _hash(expected, name):
            raise ReleaseAssetError(f"Support file checksum mismatch: {name}")

    archives = manifest.get("archives")
    if not isinstance(archives, dict) or set(archives) != {"llama", "cuda", "vulkan"}:
        raise ReleaseAssetError("Release archive declarations are invalid")
    for label, value in archives.items():
        if not isinstance(value, dict):
            raise ReleaseAssetError(f"Invalid {label} archive declaration")
        filename = _direct_filename(value.get("filename"), label)
        expected = _hash(value.get("sha256"), label)
        if archives_dir is not None and sha256(Path(archives_dir) / filename) != expected:
            raise ReleaseAssetError(f"Archive checksum mismatch: {filename}")

    model = manifest.get("model")
    if not isinstance(model, dict):
        raise ReleaseAssetError("Release model declaration is invalid")
    for name_key, hash_key in (("filename", "sha256"), ("mmproj_filename", "mmproj_sha256")):
        filename = _direct_filename(model.get(name_key), name_key)
        expected = _hash(model.get(hash_key), hash_key)
        if model_dir is not None and sha256(Path(model_dir) / filename) != expected:
            raise ReleaseAssetError(f"Model checksum mismatch: {filename}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--archives-dir")
    parser.add_argument("--model-dir")
    arguments = parser.parse_args()
    validate_release_manifest(
        arguments.manifest,
        archives_dir=arguments.archives_dir,
        model_dir=arguments.model_dir,
    )
    print("Qwen3-TTS release assets are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
