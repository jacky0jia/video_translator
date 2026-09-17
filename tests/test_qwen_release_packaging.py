import json
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging" / "llama-tts" / "package-manifest.b10792.json"
SPEC = importlib.util.spec_from_file_location(
    "validate_qwen_release", ROOT / "packaging" / "validate_qwen_release.py"
)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
ReleaseAssetError = VALIDATOR.ReleaseAssetError
validate_release_manifest = VALIDATOR.validate_release_manifest


def test_tracked_release_assets_are_complete_and_attested():
    manifest = validate_release_manifest(MANIFEST)
    assert manifest["runtime_version"] == "b10792-c5a5535e6"
    assert manifest["package_revision"] == 3
    assert set(manifest["support_files"]) == {
        "LICENSE-llama.cpp", "NOTICE.md", "runtime-manifest.b10792.json",
        "runtime-manifest-vulkan.b10792.json",
    }
    from app.api import config
    from app.services.tts import qwen_installer

    assert manifest["archives"]["llama"]["sha256"] == qwen_installer.LLAMA_ARCHIVE_SHA256
    assert manifest["archives"]["cuda"]["sha256"] == qwen_installer.CUDA_ARCHIVE_SHA256
    assert manifest["archives"]["vulkan"]["sha256"] == qwen_installer.VULKAN_ARCHIVE_SHA256
    assert manifest["model"]["filename"] == config._QWEN_MODEL_NAME
    assert manifest["model"]["sha256"] == config._QWEN_MODEL_SHA256
    assert manifest["model"]["mmproj_filename"] == config._QWEN_MMPROJ_NAME
    assert manifest["model"]["mmproj_sha256"] == config._QWEN_MMPROJ_SHA256


def test_external_archives_and_models_can_be_validated_for_a_release(tmp_path):
    support = tmp_path / "support"; support.mkdir()
    for name in (
        "LICENSE-llama.cpp", "NOTICE.md", "runtime-manifest.b10792.json",
        "runtime-manifest-vulkan.b10792.json",
    ):
        (support / name).write_bytes((MANIFEST.parent / name).read_bytes())
    raw = json.loads(MANIFEST.read_text())
    archives = tmp_path / "archives"; archives.mkdir()
    models = tmp_path / "models"; models.mkdir()
    for value in raw["archives"].values():
        target = archives / value["filename"]; target.write_bytes(value["filename"].encode())
        import hashlib
        value["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    for name_key, hash_key in (("filename", "sha256"), ("mmproj_filename", "mmproj_sha256")):
        target = models / raw["model"][name_key]; target.write_bytes(name_key.encode())
        import hashlib
        raw["model"][hash_key] = hashlib.sha256(target.read_bytes()).hexdigest()
    fixture_manifest = support / "package.json"
    fixture_manifest.write_text(json.dumps(raw))
    validate_release_manifest(fixture_manifest, archives_dir=archives, model_dir=models)


def test_modified_support_file_fails_release_validation(tmp_path):
    support = tmp_path / "support"; support.mkdir()
    raw = json.loads(MANIFEST.read_text())
    for name in raw["support_files"]:
        (support / name).write_bytes((MANIFEST.parent / name).read_bytes())
    (support / "NOTICE.md").write_text("modified")
    fixture_manifest = support / "package.json"
    fixture_manifest.write_text(json.dumps(raw))
    with pytest.raises(ReleaseAssetError, match="Support file checksum mismatch"):
        validate_release_manifest(fixture_manifest)


def test_unified_release_check_has_all_required_stages():
    spec = importlib.util.spec_from_file_location(
        "release_check", ROOT / "packaging" / "release_check.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    commands = module.release_commands(python="python-test", npm="npm-test")
    flattened = [" ".join(command) for command, _cwd in commands]
    assert any("compileall" in command for command in flattened)
    assert any("validate_qwen_release.py" in command for command in flattened)
    assert any("pytest -q tests" in command for command in flattened)
    assert any(command == "npm-test run build" for command in flattened)
    assert not any("npm-test audit" in command for command in flattened)
    assert any("from app.main import app" in command for command in flattened)
    real_commands = module.release_commands(
        python="python-test", npm="npm-test",
        archives_dir="C:/release/archives", model_dir="D:/models/qwen",
    )
    real_flattened = [" ".join(command) for command, _cwd in real_commands]
    assert any("validate_qwen_release.py" in command and "--archives-dir" in command for command in real_flattened)
    assert any("qwen_install_smoke.py" in command and "--device cuda" in command for command in real_flattened)
    audited_commands = module.release_commands(
        python="python-test", npm="npm-test", production_audit=True,
    )
    audited_flattened = [" ".join(command) for command, _cwd in audited_commands]
    audit_index = audited_flattened.index("npm-test audit --omit=dev --audit-level=high")
    build_index = audited_flattened.index("npm-test run build")
    assert audit_index < build_index


def test_manual_release_checklist_covers_runtime_acceptance():
    checklist = (ROOT / "packaging" / "RELEASE-CHECKLIST.md").read_text(encoding="utf-8")
    assert checklist.count("- [ ]") >= 10
    assert "release_check.py --production-audit" in checklist
    for required in ("Chinese", "English", "Japanese", "Korean", "cancellation", "rollback", "remote clients"):
        assert required in checklist


def test_ci_runs_the_unified_check_with_production_audit():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python packaging/release_check.py --production-audit" in workflow
    assert workflow.count("npm audit --omit=dev --audit-level=high") == 0


def test_release_evidence_records_commit_assets_and_exact_check_scope():
    spec = importlib.util.spec_from_file_location(
        "release_check_evidence", ROOT / "packaging" / "release_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    evidence = module.release_evidence(
        manifest, commit="a" * 40, branch="release/test", archives_validated=True,
        smoke_device="cuda", voice_matrix=True, verify_cancellation=True,
        force_cuda_failure=False,
    )
    assert evidence["git"] == {
        "commit": "a" * 40, "branch": "release/test", "tracked_worktree_clean": True,
    }
    assert evidence["checks"] == {
        "production_dependency_audit": True,
        "tracked_release_assets": True,
        "external_archives_and_models": True,
        "qwen_install_smoke": True,
        "smoke_device": "cuda",
        "voice_matrix": True,
        "cancellation_recovery": True,
        "forced_cuda_failure": False,
        "clean_application_install": False,
    }
    assert evidence["qwen_package"] == manifest


def test_release_evidence_without_external_assets_does_not_overclaim_scope():
    spec = importlib.util.spec_from_file_location(
        "release_check_evidence_minimal", ROOT / "packaging" / "release_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence = module.release_evidence(
        {}, commit="b" * 40, branch="main", archives_validated=False,
        smoke_device="cuda", voice_matrix=True, verify_cancellation=True,
        force_cuda_failure=True,
    )
    assert evidence["checks"]["external_archives_and_models"] is False
    assert evidence["checks"]["qwen_install_smoke"] is False
    assert evidence["checks"]["smoke_device"] is None
    assert evidence["checks"]["voice_matrix"] is False
    assert evidence["checks"]["cancellation_recovery"] is False
    assert evidence["checks"]["forced_cuda_failure"] is False
    assert evidence["checks"]["clean_application_install"] is False


def test_release_evidence_writer_requires_clean_tree_and_ignored_output(tmp_path, monkeypatch):
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location(
        "release_check_evidence_writer", ROOT / "packaging" / "release_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    evidence_root = tmp_path / ".codex-test-runs"
    evidence_root.mkdir()
    monkeypatch.setattr(module, "EVIDENCE_ROOT", evidence_root)
    replies = iter(("", "c" * 40 + "\n", "release/test\n"))
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=next(replies)),
    )
    target = module.write_release_evidence(
        evidence_root / "candidate.json", archives_validated=False,
        smoke_device="cuda", voice_matrix=False, verify_cancellation=False,
        force_cuda_failure=False,
    )
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["git"]["commit"] == "c" * 40
    assert not (evidence_root / ".candidate.json.tmp").exists()
    with pytest.raises(ValueError, match="inside .codex-test-runs"):
        module.write_release_evidence(
            tmp_path / "tracked.json", archives_validated=False,
            smoke_device="cuda", voice_matrix=False, verify_cancellation=False,
            force_cuda_failure=False,
        )

    monkeypatch.setattr(
        module.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=" M tracked.py\n"),
    )
    with pytest.raises(ValueError, match="clean tracked worktree"):
        module.write_release_evidence(
            evidence_root / "dirty.json", archives_validated=False,
            smoke_device="cuda", voice_matrix=False, verify_cancellation=False,
            force_cuda_failure=False,
        )


BACKEND_SPEC = importlib.util.spec_from_file_location("audit_qwen_backends", ROOT / "packaging" / "audit_qwen_backends.py")
BACKEND_AUDIT = importlib.util.module_from_spec(BACKEND_SPEC)
BACKEND_SPEC.loader.exec_module(BACKEND_AUDIT)


def candidate_fixture(tmp_path, monkeypatch, *, missing=None, extra=None):
    import hashlib
    import zipfile
    path = tmp_path / "candidate.zip"
    names = BACKEND_AUDIT.COMMON_FILES + BACKEND_AUDIT.CANDIDATES["vulkan"]["backend_files"]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            if name != missing:
                archive.writestr(name, b"fixture")
        if extra:
            archive.writestr(extra, b"extra")
    monkeypatch.setitem(BACKEND_AUDIT.CANDIDATES["vulkan"], "sha256", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_backend_inventory_does_not_claim_hardware_support_or_extract(tmp_path, monkeypatch):
    path = candidate_fixture(tmp_path, monkeypatch)
    before = path.read_bytes()
    result = BACKEND_AUDIT.audit_candidate(path, "vulkan")
    assert result["audit_status"] == "inventory_verified"
    assert result["amd_hardware_validated"] is False
    assert result["production_ready"] is False
    assert result["uncompressed_bytes"] == len(result["files"]) * len(b"fixture")
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == before


def test_backend_candidate_rejects_changed_archive(tmp_path, monkeypatch):
    path = candidate_fixture(tmp_path, monkeypatch)
    with path.open("ab") as output:
        output.write(b"tampered")
    with pytest.raises(BACKEND_AUDIT.CandidateAuditError, match="checksum"):
        BACKEND_AUDIT.audit_candidate(path, "vulkan")


@pytest.mark.parametrize("extra", ["../escape.dll", "nested/file.dll", "nested\\file.dll", "C:evil.dll", "LLAMA-TTS.EXE"])
def test_backend_inventory_rejects_ambiguous_paths(tmp_path, monkeypatch, extra):
    path = candidate_fixture(tmp_path, monkeypatch, extra=extra)
    with pytest.raises(BACKEND_AUDIT.CandidateAuditError):
        BACKEND_AUDIT.audit_candidate(path, "vulkan")


def test_backend_candidate_requires_tts_and_backend(tmp_path, monkeypatch):
    path = candidate_fixture(tmp_path, monkeypatch, missing="llama-tts.exe")
    with pytest.raises(BACKEND_AUDIT.CandidateAuditError, match="Missing"):
        BACKEND_AUDIT.audit_candidate(path, "vulkan")
    with pytest.raises(BACKEND_AUDIT.CandidateAuditError, match="Unknown"):
        BACKEND_AUDIT.audit_candidate(path, "cuda")


@pytest.mark.parametrize("backend", ["hip"])
def test_unverified_backends_are_not_exposed_by_installer(backend):
    from app.api.config import QwenTTSInstallRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        QwenTTSInstallRequest(llama_archive="candidate.zip", cuda_archive="cuda.zip", model_directory="models", device=backend)


def test_verified_vulkan_backend_is_exposed_without_cuda_archive():
    from app.api.config import QwenTTSInstallRequest
    request = QwenTTSInstallRequest(
        llama_archive="vulkan.zip", model_directory="models", device="vulkan"
    )
    assert request.cuda_archive is None


SMOKE_SPEC = importlib.util.spec_from_file_location("qwen_install_smoke", ROOT / "packaging/qwen_install_smoke.py")
INSTALL_SMOKE = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(INSTALL_SMOKE)


class SmokeWorker:
    def __init__(self, *, device="cuda", fallback=None, cancel=True):
        self.running = False
        self.calls = []
        self.cancel = cancel
        self.status = {"actual_device": device, "fallback_reason": fallback}

    async def synthesize(self, **request):
        self.calls.append(request)
        callback = request.get("should_cancel")
        if callback and self.cancel:
            from app.services.tts.base import TTSCancelled
            assert callback() is False
            self.running = True
            assert callback() is True
            self.running = False
            raise TTSCancelled("cancelled active child")
        return b"RIFF" + b"x" * 2048


def test_release_smoke_cancels_then_exercises_all_eight_presets():
    import asyncio
    worker = SmokeWorker()
    result = asyncio.run(INSTALL_SMOKE.exercise_worker(worker, "cuda", voice_matrix=True, verify_cancellation=True))
    assert result["cancellation_verified"]
    assert len(worker.calls) == 9
    assert len(result["samples"]) == 8
    assert {sample["voice"] for sample in result["samples"]} == {
        f"{lang}_{gender}_1" for lang in ("zh", "en", "ja", "ko") for gender in ("female", "male")
    }
    for request in worker.calls:
        assert request["text"] == INSTALL_SMOKE.SAMPLE_TEXTS[request["language"]]
    assert worker.calls[0]["should_cancel"]
    assert all("should_cancel" not in request for request in worker.calls[1:])


def test_smoke_does_not_claim_cancellation_if_synthesis_completed():
    import asyncio
    with pytest.raises(RuntimeError, match="did not cancel"):
        asyncio.run(INSTALL_SMOKE.exercise_worker(SmokeWorker(cancel=False), "cuda", verify_cancellation=True))


@pytest.mark.parametrize("device,fallback,expected,require", [
    ("cpu", None, "cuda", False), ("cuda", None, "cpu", True), ("cpu", None, "cpu", True),
])
def test_release_smoke_rejects_wrong_device_or_unproven_fallback(device, fallback, expected, require):
    import asyncio
    with pytest.raises(RuntimeError):
        asyncio.run(INSTALL_SMOKE.exercise_worker(SmokeWorker(device=device, fallback=fallback), expected, require_fallback=require))


def test_cpu_fallback_matrix_preserves_reason_across_requests():
    import asyncio
    worker = SmokeWorker(device="cpu", fallback="CUDA exit 1")
    result = asyncio.run(INSTALL_SMOKE.exercise_worker(worker, "cpu", voice_matrix=True, require_fallback=True))
    assert len(result["samples"]) == 8
    assert all(sample["fallback_reason"] == "CUDA exit 1" for sample in result["samples"])


def test_unified_release_check_forwards_extended_smoke_flags():
    spec = importlib.util.spec_from_file_location("release_check", ROOT / "packaging/release_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    commands = module.release_commands(archives_dir="archives", model_dir="models", voice_matrix=True,
                                       verify_cancellation=True, force_cuda_failure=True)
    command, _ = commands[-1]
    assert set(("--voice-matrix", "--verify-cancellation", "--force-cuda-failure")) <= set(command)
    with pytest.raises(ValueError, match="require"):
        module.release_commands(voice_matrix=True)
    with pytest.raises(ValueError, match="cuda"):
        module.release_commands(archives_dir="archives", model_dir="models", smoke_device="cpu", force_cuda_failure=True)
    clean_commands = module.release_commands(
        archives_dir="archives", model_dir="models", clean_app_smoke=True,
    )
    assert any(command[1] == "packaging/qwen_clean_app_smoke.py" for command, _cwd in clean_commands)
    with pytest.raises(ValueError, match="require"):
        module.release_commands(clean_app_smoke=True)
