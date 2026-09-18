"""Build and verify a relocatable Windows x64 acceptance bundle."""

from __future__ import annotations

import argparse
import csv
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

PACKAGING_DIR = str(Path(__file__).resolve().parent)
if PACKAGING_DIR not in sys.path:
    sys.path.insert(0, PACKAGING_DIR)
from portable_licenses import build_license_bundle
from upstream_policy import assert_upstream_assets_absent
from runtime_security import runtime_security_blockers


ROOT = Path(__file__).resolve().parents[1]
APP_EXCLUDED_PARTS = {"__pycache__", "frontend", "output", "temp", "uploads"}
RUNTIME_EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}
RUNTIME_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
QWEN_INSTALL_SUPPORT_FILES = (
    "runtime-manifest.b10792.json",
    "runtime-manifest-vulkan.b10792.json",
    "LICENSE-llama.cpp",
    "NOTICE.md",
)
PORTABLE_CONFIG = """CONFIG_VERSION: 1
LLM_PROVIDER: lm_studio
LM_STUDIO_BASE_URL: http://127.0.0.1:1234/v1
LM_STUDIO_MODEL: ""
LM_STUDIO_CLI_PATH: lms
LM_STUDIO_PORT: 1234
LM_STUDIO_TTL_SECONDS: 300
VERIFY_SSL: true
ASR_MODEL_SIZE: small
ASR_MODEL_PATH: models/faster-whisper-small
ASR_API_URL: null
ASR_REMOTE_MODEL: null
DEVICE_PREFERENCE: auto
COMPUTE_TYPE: auto
FFMPEG_PATH: ffmpeg/ffmpeg.exe
TTS_MODE: qwen
TTS_DEFAULT_VOICE: zh_female_1
TTS_SPEED: 1.0
QWEN_AUTO_CPU_FALLBACK: true
"""
START_BATCH = """@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
set "PYTHONPATH=%~dp0"
set "PATH=%~dp0runtime;%~dp0runtime\\Scripts;%~dp0runtime\\Library\\bin;%PATH%"
"%~dp0runtime\\python.exe" "%~dp0portable_launcher.py"
if errorlevel 1 pause
endlocal
"""
PORTABLE_README = """# Video Translator Windows x64 portable acceptance candidate

1. Extract the entire directory; do not run files inside the ZIP.
2. Double-click `start-portable.bat`. The application listens only on `127.0.0.1`, at http://127.0.0.1:8769/ by default.
3. Install and configure LM Studio separately for translation, then select a downloaded translation model in Settings.
4. This acceptance bundle includes Python, the frontend, FFmpeg, faster-whisper-small, Kokoro, the Qwen3-TTS CUDA/CPU runtime and eight preset voices.
5. Qwen can fall back to CPU according to Settings if NVIDIA CUDA is unavailable. Install the pinned Vulkan package through Settings to use the accepted AMD configuration.
6. Uploads, outputs, configuration and history stay in this directory. Stop the application before moving it.
7. Press Ctrl+C to stop. Set `APP_PORT` if the default port is occupied.

This is a local acceptance candidate, not a public distribution. The FFmpeg build, transitive Python dependencies and model attachments still require final redistribution review.
"""
PUBLIC_RELEASE_NOTE = "This package passed build-time third-party material checks. License attachments are listed in `licenses/THIRD-PARTY-MANIFEST.json`."


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_files(source: Path, destination: Path, *, excluded_parts=frozenset(), excluded_suffixes=frozenset()) -> None:
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in excluded_parts for part in relative.parts):
            continue
        if path.is_file() and path.suffix.lower() not in excluded_suffixes:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _copy_application(source: Path, destination: Path) -> None:
    """Copy backend source only; never carry task history or runtime media."""
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source)
        if any(part in APP_EXCLUDED_PARTS for part in relative.parts):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _require_file(path: Path, label: str) -> Path:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    return path


def _require_directory(path: Path, label: str) -> Path:
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"{label} is missing: {path}")
    return path


def validate_inputs(*, source_root: Path, python_runtime: Path, ffmpeg_dir: Path,
                    asr_model: Path, voices: Path, kokoro: Path, qwen_bundle: Path,
                    frontend_dist: Path, upstream_dependencies: bool = False) -> dict[str, Path]:
    paths = {
        "source_root": _require_directory(source_root, "source root"),
        "python_runtime": _require_directory(python_runtime, "Python runtime"),
        "asr_model": _require_directory(asr_model, "ASR model"),
        "voices": _require_directory(voices, "voice catalog"),
        "frontend_dist": _require_directory(frontend_dist, "frontend dist"),
    }
    if not upstream_dependencies:
        paths.update({
            "ffmpeg_dir": _require_directory(ffmpeg_dir, "FFmpeg directory"),
            "kokoro": _require_directory(kokoro, "Kokoro model"),
            "qwen_bundle": _require_directory(qwen_bundle, "Qwen bundle"),
        })
    _require_file(paths["python_runtime"] / "python.exe", "portable Python executable")
    if not upstream_dependencies:
        _require_file(paths["ffmpeg_dir"] / "ffmpeg.exe", "FFmpeg executable")
        _require_file(paths["ffmpeg_dir"] / "ffprobe.exe", "FFprobe executable")
    _require_file(paths["asr_model"] / "model.bin", "ASR model.bin")
    _require_file(paths["frontend_dist"] / "index.html", "frontend index")
    _require_file(paths["voices"] / "SOURCES.json", "voice catalog metadata")
    if not upstream_dependencies:
        _require_file(paths["qwen_bundle"] / "bundle.json", "Qwen bundle manifest")
        _require_file(paths["qwen_bundle"] / "install.json", "Qwen install metadata")
    return paths


def _copy_voice_catalog(source: Path, destination: Path) -> None:
    metadata = json.loads((source / "SOURCES.json").read_text(encoding="utf-8"))
    voices = metadata.get("voices")
    if not isinstance(voices, list) or len(voices) != 8:
        raise ValueError("Portable bundle requires the reviewed eight-voice catalog")
    destination.mkdir(parents=True)
    shutil.copy2(source / "SOURCES.json", destination / "SOURCES.json")
    for voice in voices:
        filename = voice.get("file")
        expected = voice.get("sha256")
        path = _require_file(source / str(filename), f"voice {voice.get('id')}")
        if sha256(path) != expected:
            raise ValueError(f"Voice checksum mismatch: {voice.get('id')}")
        shutil.copy2(path, destination / path.name)


def _copy_qwen_install_support(source_root: Path, destination: Path) -> None:
    source = source_root / "packaging" / "llama-tts"
    destination.mkdir(parents=True, exist_ok=True)
    for name in QWEN_INSTALL_SUPPORT_FILES:
        support = _require_file(source / name, f"Qwen install support {name}")
        shutil.copy2(support, destination / name)


def _python_inventory(python_runtime: Path) -> list[dict[str, str]]:
    script = (
        "import importlib.metadata as m,json;"
        "print(json.dumps(sorted([{'name':d.metadata.get('Name',d.metadata.get('Summary','unknown')),'version':d.version} "
        "for d in m.distributions()],key=lambda x:x['name'].lower())))"
    )
    result = subprocess.run(
        [str(python_runtime / "python.exe"), "-B", "-c", script],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return json.loads(result.stdout)


def _git_head(source_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source_root, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def _require_clean_tracked_tree(source_root: Path) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=source_root,
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    if status:
        raise RuntimeError("Commit tracked changes before building a portable candidate package")


def _qwen_model_assets(qwen_bundle: Path) -> list[Path]:
    manifest = json.loads((qwen_bundle / "bundle.json").read_text(encoding="utf-8"))
    return [
        _require_file(qwen_bundle / manifest[key], f"Qwen {key}")
        for key in ("model_path", "mmproj_path")
    ]


def _loader_excluded_parts(runtime: Path) -> set[str]:
    """Remove the whole loader wheel, and reject unexpected/shared RECORD paths."""
    excluded = {'espeakng_loader'}
    site = runtime / 'Lib' / 'site-packages'
    for info in site.glob('*.dist-info'):
        metadata = info / 'METADATA'
        if not metadata.is_file():
            continue
        name = Parser().parsestr(metadata.read_text(encoding='utf-8')).get('Name', '')
        if name.lower().replace('_', '-') != 'espeakng-loader':
            continue
        excluded.add(info.name)
        record = _require_file(info / 'RECORD', 'loader wheel RECORD')
        with record.open(encoding='utf-8', newline='') as stream:
            for row in csv.reader(stream):
                if not row:
                    continue
                parts = row[0].replace('\\', '/').split('/')
                if parts[0] not in {'espeakng_loader', info.name} or '..' in parts:
                    raise ValueError('Loader RECORD has unexpected ownership: ' + row[0])
    return excluded


def build_portable(*, output_dir: Path, source_root: Path = ROOT, python_runtime: Path,
                   ffmpeg_dir: Path, asr_model: Path, voices: Path, kokoro: Path,
                   qwen_bundle: Path, frontend_dist: Path, commit: str | None = None,
                   release_materials: Path | None = None,
                   public_distribution: bool = False,
                   upstream_dependencies: bool = False) -> Path:
    if upstream_dependencies and not public_distribution:
        raise ValueError('Upstream dependencies require public distribution mode')
    paths = validate_inputs(
        source_root=source_root, python_runtime=python_runtime, ffmpeg_dir=ffmpeg_dir,
        asr_model=asr_model, voices=voices, kokoro=kokoro,
        qwen_bundle=qwen_bundle, frontend_dist=frontend_dist,
        upstream_dependencies=upstream_dependencies,
    )
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        _copy_files(paths["python_runtime"], staging / "runtime", excluded_parts=RUNTIME_EXCLUDED_PARTS |
                    (_loader_excluded_parts(paths["python_runtime"]) if upstream_dependencies else set()),
                    excluded_suffixes=RUNTIME_EXCLUDED_SUFFIXES)
        component_assets = {"asr-model": [paths["asr_model"] / "model.bin"]}
        if not upstream_dependencies:
            component_assets.update({
                "ffmpeg": [paths["ffmpeg_dir"] / "ffmpeg.exe", paths["ffmpeg_dir"] / "ffprobe.exe"],
                "kokoro-model": sorted(path for path in paths["kokoro"].iterdir() if path.is_file()),
                "qwen-model": _qwen_model_assets(paths["qwen_bundle"]),
            })
        _, public_blockers = build_license_bundle(
            destination=staging / "licenses",
            python_runtime=staging / "runtime",
            frontend_root=paths["source_root"] / "app" / "frontend",
            qwen_bundle=None if upstream_dependencies else paths["qwen_bundle"],
            materials_root=release_materials.resolve() if release_materials else None,
            component_assets=component_assets,
        )
        if public_distribution and public_blockers:
            details = "\n- ".join(public_blockers)
            raise RuntimeError(f"Public distribution materials are incomplete:\n- {details}")
        _copy_application(paths["source_root"] / "app", staging / "app")
        _copy_files(paths["frontend_dist"], staging / "app" / "frontend" / "dist")
        if not upstream_dependencies:
            ffmpeg_target = staging / "app" / "ffmpeg"
            ffmpeg_target.mkdir(parents=True)
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                shutil.copy2(paths["ffmpeg_dir"] / name, ffmpeg_target / name)
        _copy_files(paths["asr_model"], staging / "models" / "faster-whisper-small")
        _copy_voice_catalog(paths["voices"], staging / "models" / "voices" / "librivox_public_domain")
        if not upstream_dependencies:
            _copy_files(paths["kokoro"], staging / "models" / "kokoro")
            _copy_files(paths["qwen_bundle"], staging / "models" / "qwen3-tts")
        _copy_qwen_install_support(
            paths["source_root"], staging / "packaging" / "llama-tts"
        )
        for name in ("LICENSE", "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.zh-CN.md"):
            shutil.copy2(paths["source_root"] / name, staging / name)
        shutil.copy2(paths["source_root"] / "packaging" / "portable_launcher.py", staging / "portable_launcher.py")
        shutil.copy2(paths["source_root"] / "packaging" / "portable_runtime_smoke.py", staging / "portable_runtime_smoke.py")
        (staging / "start-portable.bat").write_text(START_BATCH, encoding="utf-8", newline="\r\n")
        (staging / "config.yaml").write_text(PORTABLE_CONFIG, encoding="utf-8")
        readme = PORTABLE_README
        if upstream_dependencies:
            (staging / 'install-upstream.bat').write_text(
                '@echo off\r\ncd /d "%~dp0"\r\nset PYTHONUTF8=1\r\n"%~dp0runtime\\python.exe" -B "%~dp0install_upstream.py"\r\n',
                encoding='utf-8', newline='')
            readme = (paths['source_root'] / 'packaging' / 'UPSTREAM-INSTALL.md').read_text(encoding='utf-8')
            shutil.copy2(paths['source_root'] / 'packaging' / 'UPSTREAM-INSTALL.zh-CN.md', staging / 'README-PORTABLE.zh-CN.md')
            for name in ('install_upstream.py', 'verify_upstream_install.py', 'upstream-dependencies.json'):
                shutil.copy2(paths['source_root'] / 'packaging' / name, staging / name)
            assert_upstream_assets_absent(staging)
        elif public_distribution:
            readme = readme.replace(
                "This is a local acceptance candidate, not a public distribution. The FFmpeg build, transitive Python dependencies and model attachments still require final redistribution review.",
                PUBLIC_RELEASE_NOTE,
            )
        (staging / "README-PORTABLE.md").write_text(readme, encoding="utf-8")
        (staging / "README.md").write_text(readme, encoding="utf-8")
        inventory = _python_inventory(staging / "runtime")
        if public_distribution:
            blockers = runtime_security_blockers(inventory, staging / 'runtime')
            if blockers:
                raise RuntimeError('Runtime security checks failed: ' + '; '.join(blockers))
        (staging / "python-packages.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        files = []
        for path in sorted(item for item in staging.rglob("*") if item.is_file()):
            relative = path.relative_to(staging).as_posix()
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
        manifest = {
            "schema_version": 1,
            "product": "Video Translator",
            "platform": "windows-x86_64",
            "source_commit": commit or _git_head(paths["source_root"]),
            "distribution_scope": "public_release" if public_distribution else "local_acceptance_only",
            "public_distribution_ready": not public_blockers,
            "public_distribution_blockers": public_blockers,
            "dependency_delivery": "user_upstream_install" if upstream_dependencies else "bundled",
            "files": files,
        }
        (staging / "portable-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(staging, output_dir)
        return output_dir
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def create_zip(bundle: Path, archive: Path) -> Path:
    bundle, archive = bundle.resolve(), archive.resolve()
    if archive.exists():
        raise FileExistsError(f"Archive already exists: {archive}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(f".{archive.name}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as output:
            for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
                relative = Path(bundle.name) / path.relative_to(bundle)
                already_compressed = path.suffix.lower() in {".bin", ".dll", ".exe", ".gguf", ".onnx", ".npz", ".wav"}
                compression = zipfile.ZIP_STORED if already_compressed or path.stat().st_size > 64 * 1024 * 1024 else zipfile.ZIP_DEFLATED
                output.write(path, relative.as_posix(), compress_type=compression, compresslevel=6)
        os.replace(temporary, archive)
        return archive
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python-runtime", type=Path, required=True)
    parser.add_argument("--ffmpeg-dir", type=Path, default=ROOT / 'ffmpeg')
    parser.add_argument("--asr-model", type=Path, required=True)
    parser.add_argument("--voices", type=Path, required=True)
    parser.add_argument("--kokoro", type=Path, default=ROOT / 'models/kokoro')
    parser.add_argument("--qwen-bundle", type=Path, default=ROOT / 'models/qwen3-tts')
    parser.add_argument("--frontend-dist", type=Path, default=ROOT / "app/frontend/dist")
    parser.add_argument("--release-materials", type=Path)
    parser.add_argument("--public-distribution", action="store_true")
    parser.add_argument("--upstream-dependencies", action="store_true")
    parser.add_argument("--zip", action="store_true", dest="make_zip")
    args = parser.parse_args()
    _require_clean_tracked_tree(ROOT)
    bundle = build_portable(
        output_dir=args.output_dir, python_runtime=args.python_runtime,
        ffmpeg_dir=args.ffmpeg_dir, asr_model=args.asr_model, voices=args.voices,
        kokoro=args.kokoro, qwen_bundle=args.qwen_bundle, frontend_dist=args.frontend_dist,
        release_materials=args.release_materials, public_distribution=args.public_distribution,
        upstream_dependencies=args.upstream_dependencies,
    )
    print(bundle)
    if args.make_zip:
        print(create_zip(bundle, bundle.with_suffix(".zip")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
