"""Exercise the bundled Qwen runtime from inside a portable directory."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def prepare_environment() -> None:
    os.chdir(ROOT)
    runtime = ROOT / "runtime"
    native_paths = (runtime, runtime / "Scripts", runtime / "Library" / "bin")
    os.environ["PATH"] = os.pathsep.join(str(path) for path in native_paths) + os.pathsep + os.environ.get("PATH", "")
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


async def synthesize(output: Path, requested_device: str) -> dict:
    from app.services.hardware_service import hardware_service
    from app.services.tts.base import TTSSynthesisRequest
    from app.services.tts.qwen import QwenTTSProvider
    from app.services.tts.qwen_bundle import QwenTTSBundle, qwen_worker_config_for_host

    bundle_root = ROOT / "models" / "qwen3-tts"
    voices = ROOT / "models" / "voices" / "librivox_public_domain"
    bundle_config = QwenTTSBundle.load(bundle_root, voices).worker_config
    nvidia_available = hardware_service.nvidia_info is not None
    worker_config = qwen_worker_config_for_host(
        bundle_config,
        nvidia_available=nvidia_available,
        amd_available=hardware_service.amd_info is not None,
        auto_cpu_fallback=True,
    )
    if requested_device != "auto":
        worker_config["device"] = requested_device
    provider = QwenTTSProvider(
        lambda: worker_config
    )
    try:
        result = await provider.synthesize(
            TTSSynthesisRequest(
                text="これはポータブル版の音声テストです。",
                voice="ja_male_1",
                language="ja",
                speed=1.0,
                sample_rate=22050,
            )
        )
        output.write_bytes(result.audio)
        return {
            "device": worker_config["device"],
            "bytes": len(result.audio),
            "sha256": hashlib.sha256(result.audio).hexdigest(),
            "sample_rate": result.sample_rate,
            "duration_seconds": result.duration_seconds,
        }
    finally:
        await provider.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()
    output = args.output.resolve()
    prepare_environment()
    output.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(asyncio.run(synthesize(output, args.device)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
