import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import app.services.tts.qwen_installer as installer
from app.services.tts.qwen_bundle import QwenTTSBundle
from app.services.tts.qwen_installer import (
    QwenTTSInstallError,
    assemble_qwen_tts_bundle,
    inspect_qwen_tts_installation,
    preflight_qwen_tts_bundle,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path: Path, bad_member: str | None = None):
    model, mmproj = tmp_path / "model.gguf", tmp_path / "mmproj.gguf"
    model.write_bytes(b"model"); mmproj.write_bytes(b"projector")
    binary = b"runtime"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "runtime_version": "test", "executable": "llama-tts.exe", "files": {"llama-tts.exe": hashlib.sha256(binary).hexdigest()}}))
    llama, cuda = tmp_path / "llama.zip", tmp_path / "cuda.zip"
    with zipfile.ZipFile(llama, "w") as archive:
        archive.writestr("llama-tts.exe", binary)
        archive.writestr("LICENSE-LLVM-OpenMP", "Apache-2.0 WITH LLVM-exception")
        if bad_member: archive.writestr(bad_member, b"bad")
    with zipfile.ZipFile(cuda, "w"): pass
    license_file, notice = tmp_path / "LICENSE", tmp_path / "NOTICE"
    license_file.write_text("MIT"); notice.write_text("notice")
    return dict(llama_archive=llama, cuda_archive=cuda, model_path=model,
        model_sha256=digest(model), mmproj_path=mmproj, mmproj_sha256=digest(mmproj),
        destination=tmp_path / "installed", manifest_path=manifest,
        llama_license_path=license_file, notice_path=notice,
        llama_archive_sha256=digest(llama), cuda_archive_sha256=digest(cuda))


def as_vulkan(args: dict) -> dict:
    manifest_path = args["manifest_path"]
    manifest = json.loads(manifest_path.read_text())
    vulkan_binary = b"vulkan backend"
    manifest["files"]["ggml-vulkan.dll"] = hashlib.sha256(vulkan_binary).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with zipfile.ZipFile(args["llama_archive"], "a") as archive:
        archive.writestr("ggml-vulkan.dll", vulkan_binary)
    args.update(
        device="vulkan", cuda_archive=None,
        llama_archive_sha256=digest(args["llama_archive"]),
    )
    return args


def test_assembles_relocatable_verified_bundle(tmp_path):
    args = fixture(tmp_path)
    installed = assemble_qwen_tts_bundle(**args)
    raw = json.loads((installed / "bundle.json").read_text())
    assert all(not Path(raw[key]).is_absolute() for key in ("runtime_root", "runtime_manifest", "model_path", "mmproj_path"))
    catalog = tmp_path / "voices"; catalog.mkdir()
    assert QwenTTSBundle.load(installed, catalog).worker_config["device"] == "cuda"
    assert (installed / "runtime" / "LICENSE-LLVM-OpenMP").is_file()
    metadata = json.loads((installed / "install.json").read_text())
    assert metadata["schema_version"] == 1
    assert metadata["package_revision"] == 3
    assert metadata["layout_version"] == 2
    assert metadata["backend"] == "cuda"
    assert metadata["runtime_version"] == "test"


def test_rejects_archive_traversal_without_replacing_existing_bundle(tmp_path):
    args = fixture(tmp_path, "../escape.dll")
    args["destination"].mkdir(); marker = args["destination"] / "old"; marker.write_text("safe")
    with pytest.raises(QwenTTSInstallError, match="Unsafe archive member"):
        assemble_qwen_tts_bundle(**args)
    assert marker.read_text() == "safe"
    assert not (tmp_path / "escape.dll").exists()


def test_rejects_model_checksum_before_touching_destination(tmp_path):
    args = fixture(tmp_path); args["model_sha256"] = "0" * 64
    with pytest.raises(QwenTTSInstallError, match="Model checksum"):
        assemble_qwen_tts_bundle(**args)
    assert not args["destination"].exists()


def test_revision_one_upgrade_activation_failure_restores_previous_bundle(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    destination = args["destination"]
    destination.mkdir()
    (destination / "install.json").write_text(json.dumps({
        "schema_version": 1, "package_revision": 1, "runtime_version": "old-runtime",
    }), encoding="utf-8")
    (destination / "bundle.json").write_text('{"old": true}\n', encoding="utf-8")
    old_runtime = destination / "runtime"
    old_runtime.mkdir()
    (old_runtime / "old.dll").write_bytes(b"old-runtime-binary")
    before = {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in destination.rglob("*") if path.is_file()
    }
    real_replace = installer.os.replace
    calls = 0

    def fail_new_activation(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated activation failure")
        return real_replace(source, target)

    monkeypatch.setattr(installer.os, "replace", fail_new_activation)
    with pytest.raises(QwenTTSInstallError, match="new bundle activation"):
        assemble_qwen_tts_bundle(**args)
    after = {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in destination.rglob("*") if path.is_file()
    }
    assert after == before
    assert json.loads((destination / "install.json").read_text(encoding="utf-8"))["package_revision"] == 1
    assert not list(tmp_path.glob(".installed-*"))


def test_rejects_install_before_copy_when_disk_space_is_insufficient(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    usage = type("Usage", (), {"free": 1})()
    monkeypatch.setattr(installer.shutil, "disk_usage", lambda _path: usage)
    with pytest.raises(QwenTTSInstallError, match="Insufficient disk space"):
        assemble_qwen_tts_bundle(**args)
    assert not args["destination"].exists()


def test_does_not_downgrade_a_newer_installed_package(tmp_path):
    args = fixture(tmp_path)
    destination = args["destination"]
    destination.mkdir()
    (destination / "install.json").write_text(json.dumps({
        "schema_version": 1, "package_revision": 999,
    }))
    with pytest.raises(QwenTTSInstallError, match="newer Qwen3-TTS package"):
        assemble_qwen_tts_bundle(**args)
    assert json.loads((destination / "install.json").read_text())["package_revision"] == 999


def test_revision_one_metadata_migrates_through_current_revision():
    metadata = {"schema_version": 1, "package_revision": 1}
    migrated = installer.migrate_install_metadata(metadata)
    assert migrated == {
        "schema_version": 1, "package_revision": 3, "layout_version": 2,
        "backend": "cuda",
    }
    assert metadata["package_revision"] == 1


def test_vulkan_bundle_uses_one_isolated_archive_and_records_backend(tmp_path):
    args = as_vulkan(fixture(tmp_path))
    installed = assemble_qwen_tts_bundle(**args)
    metadata = json.loads((installed / "install.json").read_text())
    assert metadata["backend"] == "vulkan"
    assert metadata["device"] == "vulkan"
    assert (installed / "runtime" / "ggml-vulkan.dll").read_bytes() == b"vulkan backend"
    assert not list((installed / "runtime").glob("*cuda*"))


def test_vulkan_upgrade_activation_failure_restores_revision_two_cuda_bundle(
    tmp_path, monkeypatch
):
    args = as_vulkan(fixture(tmp_path))
    destination = args["destination"]
    destination.mkdir()
    (destination / "install.json").write_text(json.dumps({
        "schema_version": 1, "package_revision": 2, "runtime_version": "old",
        "device": "cuda",
    }))
    (destination / "bundle.json").write_text('{"device": "cuda"}\n')
    (destination / "runtime").mkdir()
    (destination / "runtime" / "ggml-cuda.dll").write_bytes(b"old cuda")
    before = {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in destination.rglob("*") if path.is_file()
    }
    real_replace = installer.os.replace
    calls = 0

    def fail_activation(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated Vulkan activation failure")
        return real_replace(source, target)

    monkeypatch.setattr(installer.os, "replace", fail_activation)
    with pytest.raises(QwenTTSInstallError, match="new bundle activation"):
        assemble_qwen_tts_bundle(**args)
    after = {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in destination.rglob("*") if path.is_file()
    }
    assert after == before
    assert not list(tmp_path.glob(".installed-*"))


def test_activation_retries_a_transient_windows_directory_lock(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    real_replace = installer.os.replace
    attempts = 0
    sleeps = []

    def locked_then_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise PermissionError(13, "directory is temporarily locked")
        return real_replace(source, target)

    monkeypatch.setattr(installer.os, "replace", locked_then_replace)
    monkeypatch.setattr(installer.time, "sleep", sleeps.append)

    installed = assemble_qwen_tts_bundle(**args)

    assert installed == args["destination"]
    assert attempts == 3
    assert sleeps == [0.1, 0.2]
    assert json.loads((installed / "install.json").read_text())["package_revision"] == 3


def test_completed_activation_is_not_rejected_when_old_backup_cleanup_is_locked(
    tmp_path, monkeypatch, caplog
):
    args = fixture(tmp_path)
    destination = args["destination"]
    destination.mkdir()
    (destination / "install.json").write_text(json.dumps({
        "schema_version": 1, "package_revision": 2, "runtime_version": "old",
        "device": "cuda",
    }))
    (destination / "old-marker").write_text("old")

    def fail_cleanup(_path):
        raise PermissionError(13, "backup is temporarily locked")

    monkeypatch.setattr(installer, "_remove_tree_with_lock_retry", fail_cleanup)

    installed = assemble_qwen_tts_bundle(**args)

    assert installed == destination
    assert not (installed / "old-marker").exists()
    assert json.loads((installed / "install.json").read_text())["package_revision"] == 3
    assert "old bundle cleanup is pending (OS error 13)" in caplog.text
    backups = list(tmp_path.glob(".installed-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "old-marker").read_text() == "old"


def test_assembly_error_identifies_stage_without_disclosing_source_path(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    private_model_path = str(args["model_path"])
    real_copy = installer.shutil.copy2

    def fail_model_copy(source, target, *copy_args, **copy_kwargs):
        if Path(source) == args["model_path"]:
            raise PermissionError(13, f"cannot read {private_model_path}")
        return real_copy(source, target, *copy_args, **copy_kwargs)

    monkeypatch.setattr(installer.shutil, "copy2", fail_model_copy)

    with pytest.raises(
        QwenTTSInstallError,
        match=r"Bundle assembly failed during model copy \(OS error 13\)",
    ) as raised:
        assemble_qwen_tts_bundle(**args)

    assert private_model_path not in str(raised.value)


def test_unknown_older_revision_is_not_guessed():
    with pytest.raises(QwenTTSInstallError, match="No Qwen3-TTS migration"):
        installer.migrate_install_metadata({"schema_version": 1, "package_revision": 0})


def test_preflight_reports_capacity_without_creating_destination(tmp_path):
    args = fixture(tmp_path)
    preflight_args = {key: args[key] for key in (
        "llama_archive", "cuda_archive", "model_path", "mmproj_path", "destination", "manifest_path",
    )}
    result = preflight_qwen_tts_bundle(**preflight_args)
    assert result["ready"] is True
    assert result["required_bytes"] > result["estimated_bytes"] > 0
    assert not args["destination"].exists()


def test_vulkan_preflight_uses_one_archive(tmp_path):
    args = as_vulkan(fixture(tmp_path))
    preflight_args = {key: args[key] for key in (
        "llama_archive", "cuda_archive", "model_path", "mmproj_path", "destination",
        "manifest_path", "device",
    )}
    result = preflight_qwen_tts_bundle(**preflight_args)
    assert result["ready"] is True
    assert result["estimated_bytes"] > 0
    assert not args["destination"].exists()


def test_status_distinguishes_managed_legacy_and_missing_bundles(tmp_path):
    destination = tmp_path / "bundle"
    assert inspect_qwen_tts_installation(destination) == {"installed": False, "managed": False}
    destination.mkdir(); (destination / "bundle.json").write_text("{}")
    assert inspect_qwen_tts_installation(destination) == {"installed": True, "managed": False}
    (destination / "runtime").mkdir(); (destination / "runtime" / "runtime.json").write_text("{}")
    (destination / "install.json").write_text(json.dumps({
        "schema_version": 1, "package_revision": 2, "runtime_version": "test",
        "model_sha256": "a" * 64, "mmproj_sha256": "b" * 64,
        "device": "cuda", "installed_bytes": 123,
    }))
    status = inspect_qwen_tts_installation(destination)
    assert status["installed"] is True and status["managed"] is True
    assert status["installed_bytes"] == 123
