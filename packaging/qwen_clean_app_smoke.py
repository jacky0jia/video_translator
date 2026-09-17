"""Install Qwen through application APIs in a disposable clean application root."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.config import (
    QwenTTSInstallRequest,
    get_qwen_tts_install_status,
    install_qwen_tts,
    preflight_qwen_tts,
)
from app.api.dubbing import list_voices
from app.core.config import settings
from app.services.dubbing_service import dubbing_service
from app.services.tts.base import TTSSynthesisRequest


PACKAGE_FILES = (
    "runtime-manifest.b10792.json",
    "runtime-manifest-vulkan.b10792.json",
    "LICENSE-llama.cpp",
    "NOTICE.md",
)


def validate_result(
    preflight: dict, installed: dict, status: dict, voices: dict, audio: bytes,
    expected_device: str,
) -> None:
    if not preflight.get("ready"):
        raise RuntimeError("Clean application preflight did not pass")
    if not installed.get("installed") or not installed.get("managed"):
        raise RuntimeError("Clean application API did not report a managed installation")
    if status.get("package_revision") != 3 or status.get("device") != expected_device:
        raise RuntimeError(f"Unexpected clean installation status: {status}")
    catalog = voices.get("voices", [])
    expected = {f"{language}_{gender}_1" for language in ("zh", "en", "ja", "ko") for gender in ("female", "male")}
    actual = {voice.get("id") for voice in catalog}
    if voices.get("tts_mode") != "qwen" or actual != expected:
        raise RuntimeError(f"Clean application voice catalog is incomplete: {sorted(actual)}")
    if voices.get("online_languages") or len(audio) < 1024:
        raise RuntimeError("Clean application Qwen boundary is invalid")


async def run_smoke(
    archives_dir: Path, model_dir: Path, workspace: Path, device: str = "cuda"
) -> dict:
    workspace = workspace.resolve(strict=True)
    allowed_root = (ROOT / ".codex-test-runs").resolve(strict=True)
    try:
        workspace.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("Clean application workspace must be inside .codex-test-runs") from exc
    release_root = ROOT / "packaging" / "llama-tts"
    release = json.loads((release_root / "package-manifest.b10792.json").read_text(encoding="utf-8"))
    previous = (settings.BASE_DIR, settings.TTS_MODE)
    with tempfile.TemporaryDirectory(prefix="clean-app-", dir=workspace) as directory:
        clean_root = Path(directory)
        clean_app = clean_root / "app"
        clean_app.mkdir()
        clean_package = clean_root / "packaging" / "llama-tts"
        clean_package.mkdir(parents=True)
        for name in PACKAGE_FILES:
            shutil.copy2(release_root / name, clean_package / name)
        shutil.copytree(
            ROOT / "models" / "voices" / "librivox_public_domain",
            clean_root / "models" / "voices" / "librivox_public_domain",
        )
        settings.BASE_DIR, settings.TTS_MODE = clean_app, "qwen"
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        vulkan = device == "vulkan"
        payload = QwenTTSInstallRequest(
            llama_archive=str(archives_dir / release["archives"]["vulkan" if vulkan else "llama"]["filename"]),
            cuda_archive=None if vulkan else str(archives_dir / release["archives"]["cuda"]["filename"]),
            model_directory=str(model_dir),
            device=device,
        )
        provider = None
        try:
            preflight = await preflight_qwen_tts(request, payload)
            installed = await install_qwen_tts(request, payload)
            status = await get_qwen_tts_install_status(request)
            voices = await list_voices()
            provider = dubbing_service.tts_registry.create("qwen")
            result = await provider.synthesize(TTSSynthesisRequest(
                text="这是干净应用目录安装验收。", voice="zh_female_1",
                language="zh", sample_rate=24_000,
            ))
            validate_result(preflight, installed, status, voices, result.audio, device)
            summary = {
                "package_revision": status["package_revision"],
                "runtime_version": status["runtime_version"],
                "device": status["device"],
                "voice_count": len(voices["voices"]),
                "languages": sorted({voice["language"] for voice in voices["voices"]}),
                "synthesis_bytes": len(result.audio),
            }
        finally:
            if provider is not None:
                await provider.close()
            settings.BASE_DIR, settings.TTS_MODE = previous
    if clean_root.exists():
        raise RuntimeError("Clean application smoke left a temporary application directory")
    print(json.dumps(summary, indent=2), flush=True)
    print("Clean application Qwen install smoke passed", flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archives-dir", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--workspace", default=ROOT / ".codex-test-runs", type=Path)
    parser.add_argument("--device", choices=("cuda", "vulkan", "cpu"), default="cuda")
    arguments = parser.parse_args()
    asyncio.run(run_smoke(
        arguments.archives_dir, arguments.model_dir, arguments.workspace, arguments.device
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
