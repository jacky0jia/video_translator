"""Verified, transactional assembler for the private Qwen3-TTS bundle."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Callable


LLAMA_ARCHIVE_SHA256 = "9b0f2b64511d36688427daeada6f001ba544b5bcb5f5405fe86a0be4b230461d"
CUDA_ARCHIVE_SHA256 = "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"
VULKAN_ARCHIVE_SHA256 = "c55e5ce547153d21ff2ab4f19fada11e808f7372db8c57f69ca721d5976b2613"
INSTALL_SCHEMA_VERSION = 1
PACKAGE_REVISION = 3
DISK_RESERVE_BYTES = 256 * 1024 * 1024
WINDOWS_LOCK_RETRY_SECONDS = (0.1, 0.2, 0.4, 0.8, 1.0)

logger = logging.getLogger(__name__)


class QwenTTSInstallError(RuntimeError):
    pass


Migration = Callable[[dict], dict]
_MIGRATIONS: dict[int, Migration] = {}


def register_install_migration(from_revision: int):
    def decorator(handler: Migration) -> Migration:
        if from_revision in _MIGRATIONS:
            raise RuntimeError(f"Duplicate Qwen3-TTS migration: {from_revision}")
        _MIGRATIONS[from_revision] = handler
        return handler
    return decorator


@register_install_migration(1)
def _migrate_revision_1_to_2(metadata: dict) -> dict:
    migrated = dict(metadata)
    migrated["layout_version"] = 1
    migrated["package_revision"] = 2
    return migrated


@register_install_migration(2)
def _migrate_revision_2_to_3(metadata: dict) -> dict:
    migrated = dict(metadata)
    migrated["layout_version"] = 2
    migrated["backend"] = "vulkan" if migrated.get("device") == "vulkan" else "cuda"
    migrated["package_revision"] = 3
    return migrated


def migrate_install_metadata(metadata: dict, target_revision: int = PACKAGE_REVISION) -> dict:
    migrated = dict(metadata)
    revision = migrated.get("package_revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise QwenTTSInstallError("Existing Qwen3-TTS installation metadata is invalid")
    if revision > target_revision:
        raise QwenTTSInstallError("A newer Qwen3-TTS package is already installed")
    while revision < target_revision:
        handler = _MIGRATIONS.get(revision)
        if handler is None:
            raise QwenTTSInstallError(f"No Qwen3-TTS migration is available from revision {revision}")
        migrated = handler(migrated)
        next_revision = migrated.get("package_revision")
        if next_revision != revision + 1:
            raise QwenTTSInstallError("Qwen3-TTS migration produced an invalid revision")
        revision = next_revision
    return migrated


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_archive(path: Path, expected: str) -> zipfile.ZipFile:
    if _sha256(path) != expected:
        raise QwenTTSInstallError(f"Archive checksum mismatch: {path.name}")
    archive = zipfile.ZipFile(path)
    seen: set[str] = set()
    for item in archive.infolist():
        name = item.filename.replace("\\", "/")
        pure = PurePosixPath(name)
        if name in seen or pure.is_absolute() or ".." in pure.parts:
            archive.close()
            raise QwenTTSInstallError(f"Unsafe archive member: {name}")
        seen.add(name)
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode) or (item.flag_bits & 1):
            archive.close()
            raise QwenTTSInstallError(f"Unsupported archive member: {name}")
    return archive


def _extract_attested(archives: list[zipfile.ZipFile], runtime: Path, files: dict[str, str]) -> None:
    members: dict[str, tuple[zipfile.ZipFile, zipfile.ZipInfo]] = {}
    for archive in archives:
        for item in archive.infolist():
            name = PurePosixPath(item.filename.replace("\\", "/")).name
            if name in files:
                if name in members:
                    raise QwenTTSInstallError(f"Duplicate runtime artifact: {name}")
                members[name] = (archive, item)
    if set(members) != set(files):
        missing = sorted(set(files) - set(members))
        raise QwenTTSInstallError(f"Runtime archive is incomplete: {', '.join(missing)}")
    runtime.mkdir()
    for name, expected in files.items():
        archive, item = members[name]
        target = runtime / name
        with archive.open(item) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        if _sha256(target) != expected:
            raise QwenTTSInstallError(f"Runtime artifact checksum mismatch: {name}")


def _extract_release_license(archive: zipfile.ZipFile, runtime: Path) -> None:
    matches = [item for item in archive.infolist() if PurePosixPath(item.filename).name == "LICENSE-LLVM-OpenMP"]
    if len(matches) != 1:
        raise QwenTTSInstallError("LLVM OpenMP license is missing or ambiguous")
    with archive.open(matches[0]) as source, (runtime / "LICENSE-LLVM-OpenMP").open("wb") as output:
        shutil.copyfileobj(source, output)


def _validate_upgrade(destination: Path) -> None:
    metadata_path = destination / "install.json"
    if not metadata_path.exists():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QwenTTSInstallError("Existing Qwen3-TTS installation metadata is invalid") from exc
    schema = metadata.get("schema_version") if isinstance(metadata, dict) else None
    revision = metadata.get("package_revision") if isinstance(metadata, dict) else None
    if not isinstance(schema, int) or not isinstance(revision, int):
        raise QwenTTSInstallError("Existing Qwen3-TTS installation metadata is invalid")
    if schema > INSTALL_SCHEMA_VERSION:
        raise QwenTTSInstallError("A newer Qwen3-TTS package is already installed")
    migrate_install_metadata(metadata)


def _selected_runtime_size(archives: list[zipfile.ZipFile], files: dict[str, str]) -> int:
    selected = set(files)
    return sum(
        item.file_size
        for archive in archives
        for item in archive.infolist()
        if PurePosixPath(item.filename.replace("\\", "/")).name in selected
    )


def _require_disk_space(parent: Path, required: int) -> None:
    free = shutil.disk_usage(parent).free
    if free < required:
        required_gib = required / (1024 ** 3)
        free_gib = free / (1024 ** 3)
        raise QwenTTSInstallError(
            f"Insufficient disk space: {required_gib:.2f} GiB required, {free_gib:.2f} GiB available"
        )


def _is_transient_windows_lock(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in {5, 32, 33} or exc.errno == 13


def _replace_with_lock_retry(source: Path, target: Path) -> None:
    for delay in (*WINDOWS_LOCK_RETRY_SECONDS, None):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if delay is None or not _is_transient_windows_lock(exc):
                raise
            time.sleep(delay)


def _remove_tree_with_lock_retry(path: Path) -> None:
    for delay in (*WINDOWS_LOCK_RETRY_SECONDS, None):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            if not path.exists():
                return
            if delay is None or not _is_transient_windows_lock(exc):
                raise
            time.sleep(delay)


def preflight_qwen_tts_bundle(
    *,
    llama_archive: str | Path,
    cuda_archive: str | Path | None,
    model_path: str | Path,
    mmproj_path: str | Path,
    destination: str | Path,
    manifest_path: str | Path,
    device: str = "cuda",
) -> dict:
    """Check source layout and target capacity without copying or hashing large files."""
    if device not in {"cuda", "cpu", "vulkan"}:
        raise QwenTTSInstallError("Device must be cuda, vulkan or cpu")
    if device == "vulkan" and cuda_archive:
        raise QwenTTSInstallError("Vulkan runtime must not include CUDA archives")
    if device != "vulkan" and not cuda_archive:
        raise QwenTTSInstallError("CUDA redistribution archive is required")
    llama_zip, model, mmproj, manifest_source = [
        Path(value).resolve(strict=True)
        for value in (llama_archive, model_path, mmproj_path, manifest_path)
    ]
    cuda_zip = Path(cuda_archive).resolve(strict=True) if cuda_archive else None
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_upgrade(destination)
    try:
        manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
        files = manifest["files"]
        if manifest.get("schema_version") != 1 or not isinstance(files, dict):
            raise QwenTTSInstallError("Unsupported runtime manifest")
        with ExitStack() as stack:
            archives = [stack.enter_context(zipfile.ZipFile(llama_zip))]
            if cuda_zip:
                archives.append(stack.enter_context(zipfile.ZipFile(cuda_zip)))
            available = {
                PurePosixPath(item.filename.replace("\\", "/")).name
                for archive in archives for item in archive.infolist()
            }
            missing = sorted(set(files) - available)
            if missing:
                raise QwenTTSInstallError(f"Runtime archive is incomplete: {', '.join(missing)}")
            estimated = model.stat().st_size + mmproj.stat().st_size + _selected_runtime_size(archives, files)
    except (KeyError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise QwenTTSInstallError("Qwen3-TTS installation sources are invalid") from exc
    required = estimated + DISK_RESERVE_BYTES
    free = shutil.disk_usage(destination.parent).free
    return {
        "ready": free >= required,
        "estimated_bytes": estimated,
        "required_bytes": required,
        "free_bytes": free,
    }


def inspect_qwen_tts_installation(destination: str | Path) -> dict:
    """Return non-sensitive, inexpensive status for an application-owned bundle."""
    root = Path(destination).resolve()
    if not root.exists():
        return {"installed": False, "managed": False}
    metadata_path = root / "install.json"
    if not metadata_path.is_file():
        return {"installed": (root / "bundle.json").is_file(), "managed": False}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QwenTTSInstallError("Installed Qwen3-TTS metadata is invalid") from exc
    required = {
        "schema_version": int,
        "package_revision": int,
        "runtime_version": str,
        "model_sha256": str,
        "mmproj_sha256": str,
        "device": str,
        "installed_bytes": int,
    }
    if not isinstance(metadata, dict) or any(
        not isinstance(metadata.get(key), value_type) for key, value_type in required.items()
    ):
        raise QwenTTSInstallError("Installed Qwen3-TTS metadata is invalid")
    files_ready = all((root / name).exists() for name in ("bundle.json", "runtime/runtime.json"))
    return {
        "installed": files_ready,
        "managed": True,
        "package_revision": metadata["package_revision"],
        "runtime_version": metadata["runtime_version"],
        "backend": metadata.get(
            "backend", "vulkan" if metadata["device"] == "vulkan" else "cuda"
        ),
        "device": metadata["device"],
        "installed_bytes": metadata["installed_bytes"],
    }


def assemble_qwen_tts_bundle(
    *, llama_archive: str | Path, cuda_archive: str | Path | None,
    model_path: str | Path, model_sha256: str,
    mmproj_path: str | Path, mmproj_sha256: str,
    destination: str | Path, manifest_path: str | Path,
    llama_license_path: str | Path, notice_path: str | Path,
    device: str = "cuda",
    llama_archive_sha256: str | None = None,
    cuda_archive_sha256: str = CUDA_ARCHIVE_SHA256,
) -> Path:
    """Build a relocatable bundle and replace the destination only after verification."""
    if device not in {"cuda", "vulkan", "cpu"}:
        raise QwenTTSInstallError("Device must be cuda, vulkan or cpu")
    if device == "vulkan" and cuda_archive:
        raise QwenTTSInstallError("Vulkan runtime must not include CUDA archives")
    if device != "vulkan" and not cuda_archive:
        raise QwenTTSInstallError("CUDA redistribution archive is required")
    paths = [Path(value).resolve(strict=True) for value in (
        llama_archive, model_path, mmproj_path,
        manifest_path, llama_license_path, notice_path,
    )]
    llama_zip, model, mmproj, manifest_source, license_source, notice_source = paths
    cuda_zip = Path(cuda_archive).resolve(strict=True) if cuda_archive else None
    expected_llama_hash = llama_archive_sha256 or (
        VULKAN_ARCHIVE_SHA256 if device == "vulkan" else LLAMA_ARCHIVE_SHA256
    )
    if _sha256(model) != model_sha256 or _sha256(mmproj) != mmproj_sha256:
        raise QwenTTSInstallError("Model checksum mismatch")
    manifest = json.loads(manifest_source.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), dict):
        raise QwenTTSInstallError("Unsupported runtime manifest")

    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    _validate_upgrade(destination)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    backup = destination.parent / f".{destination.name}-backup-{uuid.uuid4().hex}"
    archives: list[zipfile.ZipFile] = []
    activated = False
    stage = "archive verification"
    try:
        archives = [_checked_archive(llama_zip, expected_llama_hash)]
        if cuda_zip:
            archives.append(_checked_archive(cuda_zip, cuda_archive_sha256))
        estimated_bytes = (
            model.stat().st_size
            + mmproj.stat().st_size
            + _selected_runtime_size(archives, manifest["files"])
        )
        _require_disk_space(destination.parent, estimated_bytes + DISK_RESERVE_BYTES)
        stage = "runtime extraction"
        runtime = staging / "runtime"
        _extract_attested(archives, runtime, manifest["files"])
        _extract_release_license(archives[0], runtime)
        shutil.copy2(manifest_source, runtime / "runtime.json")
        shutil.copy2(license_source, runtime / "LICENSE-llama.cpp")
        shutil.copy2(notice_source, runtime / "NOTICE.md")
        stage = "model copy"
        model_dir = staging / "models"
        model_dir.mkdir()
        shutil.copy2(model, model_dir / model.name)
        shutil.copy2(mmproj, model_dir / mmproj.name)
        bundle = {
            "schema_version": 1, "device": device,
            "runtime_root": "runtime", "runtime_manifest": "runtime/runtime.json",
            "model_path": f"models/{model.name}", "model_sha256": model_sha256,
            "mmproj_path": f"models/{mmproj.name}", "mmproj_sha256": mmproj_sha256,
        }
        (staging / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        install_metadata = {
            "schema_version": INSTALL_SCHEMA_VERSION,
            "package_revision": PACKAGE_REVISION,
            "layout_version": 2,
            "backend": "vulkan" if device == "vulkan" else "cuda",
            "runtime_version": manifest["runtime_version"],
            "model_sha256": model_sha256,
            "mmproj_sha256": mmproj_sha256,
            "device": device,
            "installed_bytes": estimated_bytes,
        }
        (staging / "install.json").write_text(
            json.dumps(install_metadata, indent=2) + "\n", encoding="utf-8"
        )
        stage = "existing bundle backup"
        if destination.exists():
            _replace_with_lock_retry(destination, backup)
        try:
            stage = "new bundle activation"
            _replace_with_lock_retry(staging, destination)
            activated = True
        except BaseException:
            if backup.exists():
                _replace_with_lock_retry(backup, destination)
            raise
        if backup.exists():
            try:
                _remove_tree_with_lock_retry(backup)
            except OSError as exc:
                code = getattr(exc, "winerror", None) or exc.errno or "unknown"
                logger.warning(
                    "Qwen3-TTS activated but old bundle cleanup is pending (OS error %s)",
                    code,
                )
        return destination
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        code = getattr(exc, "winerror", None) or getattr(exc, "errno", None) or "unknown"
        raise QwenTTSInstallError(
            f"Bundle assembly failed during {stage} (OS error {code})"
        ) from exc
    finally:
        for archive in archives:
            archive.close()
        if not activated and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
