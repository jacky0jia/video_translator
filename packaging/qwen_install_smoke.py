"""Assemble a disposable Qwen bundle and verify synthesis and optional lifecycle cases."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.tts.qwen_bundle import QwenTTSBundle
from app.services.tts.base import TTSCancelled
from app.services.tts.qwen_installer import assemble_qwen_tts_bundle
from app.workers.llama_tts_worker import LlamaTTSWorkerClient

SAMPLE_TEXTS = {
    "zh": "这是发布安装冒烟测试。",
    "en": "This is a release installation test.",
    "ja": "これは音声合成のテストです。",
    "ko": "음성 합성 테스트입니다.",
}


async def exercise_worker(worker, expected_device, *, voice_matrix=False, verify_cancellation=False,
                          require_fallback=False):
    """Exercise one worker across cancellation and successive preset requests."""
    summary = {"cancellation_verified": False, "samples": []}
    if verify_cancellation:
        try:
            await worker.synthesize(text=SAMPLE_TEXTS["zh"], voice_id="zh_female_1", language="zh",
                                    should_cancel=lambda: worker.running)
        except TTSCancelled:
            if worker.running:
                raise RuntimeError("Cancelled worker still has a running child")
            summary["cancellation_verified"] = True
        else:
            raise RuntimeError("Cancellation smoke did not cancel an active synthesis")
    cases = [("zh", "zh_female_1")]
    if voice_matrix:
        cases = [(language, f"{language}_{gender}_1") for language in SAMPLE_TEXTS for gender in ("female", "male")]
    for language, voice in cases:
        audio = await worker.synthesize(text=SAMPLE_TEXTS[language], voice_id=voice, language=language)
        if len(audio) < 1024:
            raise RuntimeError("Qwen3-TTS smoke output is unexpectedly small")
        if worker.status["actual_device"] != expected_device:
            raise RuntimeError(f"Expected {expected_device}, got {worker.status}")
        if require_fallback and not worker.status["fallback_reason"]:
            raise RuntimeError("CUDA failure did not trigger CPU fallback")
        summary["samples"].append({"voice": voice, "language": language, "wav_bytes": len(audio),
                                   "wav_sha256": hashlib.sha256(audio).hexdigest(), "device": expected_device,
                                   "fallback_reason": worker.status["fallback_reason"]})
        print(f"Qwen3-TTS {voice} smoke passed ({len(audio)} WAV bytes, {expected_device})", flush=True)
    return summary


async def run_smoke(archives_dir: Path, model_dir: Path, device: str, *, force_cuda_failure: bool = False,
                    voice_matrix: bool = False, verify_cancellation: bool = False) -> int:
    if force_cuda_failure and device != "cuda":
        raise ValueError("CUDA failure smoke requires --device cuda")
    package_root = ROOT / "packaging" / "llama-tts"
    release = json.loads((package_root / "package-manifest.b10792.json").read_text(encoding="utf-8"))
    archives = release["archives"]
    model = release["model"]
    voices = ROOT / "models" / "voices" / "librivox_public_domain"
    with tempfile.TemporaryDirectory(prefix="subtitle-translator-qwen-release-") as directory:
        bundle_root = Path(directory) / "bundle"
        vulkan = device == "vulkan"
        await asyncio.to_thread(
            assemble_qwen_tts_bundle,
            llama_archive=archives_dir / archives["vulkan" if vulkan else "llama"]["filename"],
            cuda_archive=None if vulkan else archives_dir / archives["cuda"]["filename"],
            model_path=model_dir / model["filename"],
            model_sha256=model["sha256"],
            mmproj_path=model_dir / model["mmproj_filename"],
            mmproj_sha256=model["mmproj_sha256"],
            destination=bundle_root,
            manifest_path=package_root / (
                "runtime-manifest-vulkan.b10792.json" if vulkan
                else "runtime-manifest.b10792.json"
            ),
            llama_license_path=package_root / "LICENSE-llama.cpp",
            notice_path=package_root / "NOTICE.md",
            device=device,
        )
        config = QwenTTSBundle.load(bundle_root, voices).worker_config
        children = []
        async def process_factory(*args, **kwargs):
            if force_cuda_failure and "CUDA0" in args:
                # Hide CUDA devices only from this child; the CPU retry uses the real binary.
                kwargs["env"] = dict(kwargs["env"], CUDA_VISIBLE_DEVICES="-1")
            child = await asyncio.create_subprocess_exec(*args, **kwargs)
            children.append((child, Path(kwargs["cwd"])))
            return child

        worker = LlamaTTSWorkerClient(request_timeout=300, process_factory=process_factory)
        try:
            await worker.start(config)
            expected_device = "cpu" if force_cuda_failure else device
            summary = await exercise_worker(worker, expected_device, voice_matrix=voice_matrix,
                                            verify_cancellation=verify_cancellation, require_fallback=force_cuda_failure)
        finally:
            await worker.close()
        if any(child.returncode is None or directory.exists() for child, directory in children):
            raise RuntimeError("Qwen smoke left a child process or temporary synthesis directory")
        summary["children_reaped"] = len(children)
        print(json.dumps(summary, indent=2), flush=True)
        print(f"Qwen3-TTS {device} install smoke passed ({len(summary['samples'])} samples)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archives-dir", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--device", choices=("cuda", "vulkan", "cpu"), default="cuda")
    parser.add_argument("--force-cuda-failure", action="store_true")
    parser.add_argument("--voice-matrix", action="store_true", help="Synthesize all eight Chinese/English/Japanese/Korean presets")
    parser.add_argument("--verify-cancellation", action="store_true", help="Cancel a child, then verify recovery with the same worker")
    arguments = parser.parse_args()
    return asyncio.run(run_smoke(arguments.archives_dir, arguments.model_dir, arguments.device,
                                force_cuda_failure=arguments.force_cuda_failure, voice_matrix=arguments.voice_matrix,
                                verify_cancellation=arguments.verify_cancellation))


if __name__ == "__main__":
    raise SystemExit(main())
