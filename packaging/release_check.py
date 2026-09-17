"""Run the repository's deterministic release acceptance checks."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "packaging" / "llama-tts" / "package-manifest.b10792.json"
EVIDENCE_ROOT = ROOT / ".codex-test-runs"


def release_evidence(
    manifest: dict,
    *,
    commit: str,
    branch: str,
    archives_validated: bool,
    smoke_device: str,
    voice_matrix: bool,
    verify_cancellation: bool,
    force_cuda_failure: bool,
    clean_app_smoke: bool = False,
) -> dict:
    return {
        "schema_version": 1,
        "git": {"commit": commit, "branch": branch, "tracked_worktree_clean": True},
        "checks": {
            "production_dependency_audit": True,
            "tracked_release_assets": True,
            "external_archives_and_models": archives_validated,
            "qwen_install_smoke": archives_validated,
            "smoke_device": smoke_device if archives_validated else None,
            "voice_matrix": archives_validated and voice_matrix,
            "cancellation_recovery": archives_validated and verify_cancellation,
            "forced_cuda_failure": archives_validated and force_cuda_failure,
            "clean_application_install": archives_validated and clean_app_smoke,
        },
        "qwen_package": manifest,
    }


def write_release_evidence(
    output: str | Path,
    *,
    archives_validated: bool,
    smoke_device: str,
    voice_matrix: bool,
    verify_cancellation: bool,
    force_cuda_failure: bool,
    clean_app_smoke: bool = False,
) -> Path:
    target = Path(output).resolve()
    try:
        target.relative_to(EVIDENCE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Release evidence output must be inside .codex-test-runs") from exc
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if status:
        raise ValueError("Release evidence requires a clean tracked worktree")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    evidence = release_evidence(
        manifest, commit=commit, branch=branch, archives_validated=archives_validated,
        smoke_device=smoke_device, voice_matrix=voice_matrix,
        verify_cancellation=verify_cancellation, force_cuda_failure=force_cuda_failure,
        clean_app_smoke=clean_app_smoke,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def release_commands(
    python: str = sys.executable,
    npm: str | None = None,
    *,
    archives_dir: str | None = None,
    model_dir: str | None = None,
    smoke_device: str = "cuda",
    voice_matrix: bool = False,
    verify_cancellation: bool = False,
    force_cuda_failure: bool = False,
    production_audit: bool = False,
    clean_app_smoke: bool = False,
) -> list[tuple[list[str], Path]]:
    if bool(archives_dir) != bool(model_dir):
        raise ValueError("Archive and model directories must be provided together")
    if (voice_matrix or verify_cancellation or force_cuda_failure or clean_app_smoke) and not archives_dir:
        raise ValueError("Extended Qwen smoke checks require archive and model directories")
    if force_cuda_failure and smoke_device != "cuda":
        raise ValueError("CUDA failure smoke requires the cuda device")
    npm_command = npm or shutil.which("npm") or "npm"
    commands = [
        ([python, "-m", "compileall", "-q", "app", "packaging"], ROOT),
        ([python, "packaging/validate_qwen_release.py", "--manifest", "packaging/llama-tts/package-manifest.b10792.json"], ROOT),
        ([python, "-m", "pytest", "-q", "tests"], ROOT),
    ]
    if production_audit:
        commands.append(([npm_command, "audit", "--omit=dev", "--audit-level=high"], ROOT / "app" / "frontend"))
    commands.extend([
        ([npm_command, "run", "build"], ROOT / "app" / "frontend"),
        ([python, "-c", "from app.main import app; assert app.title"], ROOT),
    ])
    if archives_dir and model_dir:
        extra_flags = [flag for enabled, flag in (
            (voice_matrix, "--voice-matrix"), (verify_cancellation, "--verify-cancellation"),
            (force_cuda_failure, "--force-cuda-failure"),
        ) if enabled]
        commands.extend([
            ([python, "packaging/validate_qwen_release.py", "--manifest", "packaging/llama-tts/package-manifest.b10792.json", "--archives-dir", archives_dir, "--model-dir", model_dir], ROOT),
            ([python, "packaging/qwen_install_smoke.py", "--archives-dir", archives_dir, "--model-dir", model_dir, "--device", smoke_device, *extra_flags], ROOT),
        ])
        if clean_app_smoke:
            commands.append(([
                python, "packaging/qwen_clean_app_smoke.py",
                "--archives-dir", archives_dir, "--model-dir", model_dir,
                "--device", smoke_device,
            ], ROOT))
    return commands


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qwen-archives-dir")
    parser.add_argument("--qwen-model-dir")
    parser.add_argument("--qwen-smoke-device", choices=("cuda", "vulkan", "cpu"), default="cuda")
    parser.add_argument("--qwen-voice-matrix", action="store_true")
    parser.add_argument("--qwen-verify-cancellation", action="store_true")
    parser.add_argument("--qwen-force-cuda-failure", action="store_true")
    parser.add_argument("--qwen-clean-app-smoke", action="store_true")
    parser.add_argument(
        "--production-audit", action="store_true",
        help="Query the npm advisory service for high-severity production dependency findings",
    )
    parser.add_argument(
        "--evidence-output",
        help="After all checks pass, write release evidence under .codex-test-runs",
    )
    arguments = parser.parse_args()
    if bool(arguments.qwen_archives_dir) != bool(arguments.qwen_model_dir):
        parser.error("--qwen-archives-dir and --qwen-model-dir must be provided together")
    if arguments.evidence_output and not arguments.production_audit:
        parser.error("--evidence-output requires --production-audit")
    try:
        commands = release_commands(
            archives_dir=arguments.qwen_archives_dir,
            model_dir=arguments.qwen_model_dir,
            smoke_device=arguments.qwen_smoke_device,
            voice_matrix=arguments.qwen_voice_matrix,
            verify_cancellation=arguments.qwen_verify_cancellation,
            force_cuda_failure=arguments.qwen_force_cuda_failure,
            production_audit=arguments.production_audit,
            clean_app_smoke=arguments.qwen_clean_app_smoke,
        )
    except ValueError as exc:
        parser.error(str(exc))
    for command, cwd in commands:
        print(f"[release-check] {' '.join(command)}", flush=True)
        subprocess.run(command, cwd=cwd, check=True)
    if arguments.evidence_output:
        try:
            evidence_path = write_release_evidence(
                arguments.evidence_output,
                archives_validated=bool(arguments.qwen_archives_dir),
                smoke_device=arguments.qwen_smoke_device,
                voice_matrix=arguments.qwen_voice_matrix,
                verify_cancellation=arguments.qwen_verify_cancellation,
                force_cuda_failure=arguments.qwen_force_cuda_failure,
                clean_app_smoke=arguments.qwen_clean_app_smoke,
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Release evidence written to {evidence_path}", flush=True)
    print("Release acceptance checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
