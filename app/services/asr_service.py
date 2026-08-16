import json
import gc
import logging
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, List

import httpx

from faster_whisper import WhisperModel

from app.core.config import settings
from app.core.provider_lifecycle import NoopProviderLifecycle, ProviderLifecycle, provider_stage
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.hardware_service import hardware_service

logger = logging.getLogger(__name__)


class ASRService:
    def __init__(
        self,
        *,
        model_factory: Callable = WhisperModel,
        http_client_factory: Callable = httpx.AsyncClient,
        lifecycle: ProviderLifecycle | None = None,
        worker_factory: Callable | None = None,
    ):
        self.model = None
        self._model_factory = model_factory
        self._http_client_factory = http_client_factory
        self._lifecycle = lifecycle or NoopProviderLifecycle()
        self._worker_factory = worker_factory
        self._worker = None

    @asynccontextmanager
    async def stage(self, task_id: str | None = None):
        """Keep one model for all chunks, then unload it before releasing GPU."""
        provider = "remote_openai_compatible" if settings.ASR_API_URL else "faster_whisper"
        before = self._gpu_memory_snapshot() if provider == "faster_whisper" else None
        if before is not None:
            logger.info("ASR GPU memory before stage: %s MiB", before)
        async with provider_stage(
            self._lifecycle, task_id=task_id, stage="asr", provider=provider
        ):
            try:
                if provider == "faster_whisper" and self._worker_factory:
                    device, compute_type = self._resolve_device_and_compute()
                    self._worker = self._worker_factory()
                    await self._worker.start(
                        model_args={
                            "model_size_or_path": self._resolve_model_path(),
                            "device": device, "compute_type": compute_type,
                        },
                        transcribe_args={
                            "beam_size": settings.ASR_BEAM_SIZE,
                            "task": "transcribe",
                            "chunk_length": settings.ASR_CHUNK_LENGTH,
                        },
                    )
                yield
            finally:
                if provider == "faster_whisper":
                    if self._worker:
                        await self._worker.close()
                        self._worker = None
                    unloaded = self.unload()
                    after = self._gpu_memory_snapshot()
                    logger.info(
                        "ASR model unload complete (native_unload=%s); GPU memory after stage: %s MiB",
                        unloaded, after if after is not None else "unavailable",
                    )

    @staticmethod
    def _gpu_memory_snapshot() -> int | None:
        """Return total used NVIDIA memory for an auditable stage log."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            values = [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]
            return sum(values) if values else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def unload(self) -> bool:
        """Drop faster-whisper/CTranslate2 model state and all service references."""
        model = self.model
        self.model = None
        if model is None:
            return False

        native_unloaded = False
        engine = getattr(model, "model", None)
        unload_model = getattr(engine, "unload_model", None)
        if callable(unload_model):
            try:
                unload_model()
                native_unloaded = True
            except Exception:
                logger.exception("CTranslate2 native model unload failed")
        del engine
        del model
        gc.collect()
        return native_unloaded

    def _infer_model_size(self) -> str:
        """Infer model size from ASR_MODEL_PATH folder name, or fall back to ASR_MODEL_SIZE."""
        import re
        if settings.ASR_MODEL_PATH:
            path = Path(settings.ASR_MODEL_PATH)
            match = re.search(r'whisper-(\w+)', path.name)
            if match:
                return match.group(1)
        return settings.ASR_MODEL_SIZE or "base"

    def _resolve_device_and_compute(self) -> tuple:
        """
        Resolve device and compute_type based on settings and hardware detection.
        Returns (device, compute_type) for faster-whisper.
        """
        preference = settings.DEVICE_PREFERENCE
        compute = settings.COMPUTE_TYPE

        # Map user-friendly 'gpu' to faster-whisper's 'cuda'
        device = "cuda" if preference == "gpu" else preference

        # Fail fast if GPU is forced but no compatible GPU is present
        if preference == "gpu" and hardware_service.nvidia_info is None:
            raise RuntimeError(
                "GPU mode is forced (DEVICE_PREFERENCE=gpu) but no NVIDIA CUDA GPU was detected. "
                "Please set DEVICE_PREFERENCE to 'auto' or 'cpu'."
            )

        # Auto-detection
        if preference == "auto" or compute == "auto":
            # Use float16 as baseline for VRAM estimation when compute is auto
            baseline_compute = compute if compute != "auto" else "float16"
            rec_device, rec_compute, reason = hardware_service.recommend_device(
                self._infer_model_size(), baseline_compute
            )
            if preference == "auto":
                device = rec_device
            if compute == "auto":
                compute = rec_compute
            logger.info(f"Hardware auto-detection: {reason}")

        return device, compute

    def _get_model(self):
        """Lazy load the model with status logging."""
        if self.model is None:
            device, compute_type = self._resolve_device_and_compute()
            model_path = settings.ASR_MODEL_PATH

            try:
                if model_path:
                    import os
                    import re
                    if not os.path.isdir(model_path):
                        raise FileNotFoundError(
                            f"ASR model path does not exist or is not a directory: '{model_path}'. "
                            f"Please check the path in Advanced Settings → ASR Model Path, "
                            f"or leave it empty to download from HuggingFace."
                        )
                    # Determine if model_path is a direct model folder or a parent directory
                    direct_model = (
                        re.search(r'whisper-\w+', Path(model_path).name) is not None
                        and (
                            (Path(model_path) / "model.bin").exists()
                            or (Path(model_path) / "config.json").exists()
                        )
                    )
                    if direct_model:
                        load_path = model_path
                        logger.info(
                            f"Loading Whisper model from direct path: {load_path} "
                            f"(device={device}, compute={compute_type})"
                        )
                    else:
                        model_size = self._infer_model_size()
                        load_path = os.path.join(model_path, f"whisper-{model_size}")
                        if not os.path.isdir(load_path):
                            raise FileNotFoundError(
                                f"Model folder not found: '{load_path}'. "
                                f"Please ensure 'whisper-{model_size}' exists under '{model_path}', "
                                f"or select the direct model folder (e.g. whisper-base) as ASR Model Path."
                            )
                        logger.info(
                            f"Loading Whisper model from constructed path: {load_path} "
                            f"(device={device}, compute={compute_type})"
                        )
                    self.model = self._model_factory(
                        load_path, device=device, compute_type=compute_type
                    )
                else:
                    model_size = self._infer_model_size()
                    logger.info(
                        f"Loading Whisper model '{model_size}' from HuggingFace "
                        f"(device={device}, compute={compute_type})..."
                    )
                    self.model = self._model_factory(
                        model_size,
                        device=device,
                        compute_type=compute_type,
                    )

                logger.info("Whisper model loaded successfully.")
            except Exception as e:
                logger.error(f"Error loading Whisper model: {e}")
                # Fallback to CPU only if we were in auto mode and not already on CPU
                if device != "cpu":
                    logger.info("Attempting fallback to CPU...")
                    if settings.ASR_MODEL_PATH:
                        fallback_path = self._resolve_model_path()
                    else:
                        fallback_path = self._infer_model_size()
                    self.model = self._model_factory(
                        fallback_path,
                        device="cpu",
                        compute_type="int8",
                    )
                else:
                    raise e
        return self.model

    def _resolve_model_path(self) -> str:
        """Resolve the effective local model path from settings."""
        import re
        model_path = settings.ASR_MODEL_PATH
        if not model_path:
            return self._infer_model_size()
        import os
        if (
            re.search(r'whisper-\w+', Path(model_path).name)
            and (
                (Path(model_path) / "model.bin").exists()
                or (Path(model_path) / "config.json").exists()
            )
        ):
            return model_path
        return os.path.join(model_path, f"whisper-{self._infer_model_size()}")

    async def _transcribe_remote(
        self, audio_path: Path, language: str = None
    ) -> TranscriptionResult:
        """Call a remote ASR API (OpenAI-compatible /v1/audio/transcriptions) instead of loading the model locally."""
        url = settings.ASR_API_URL
        if not url.endswith("/v1/audio/transcriptions"):
            url = url.rstrip("/") + "/v1/audio/transcriptions"
        logger.info(f"Using remote ASR API: {url}")

        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/wav")}
            data = {
                "model": settings.ASR_REMOTE_MODEL or "whisper-1",
                "language": language or "",
                "response_format": "verbose_json",
            }
            async with self._http_client_factory(timeout=300.0, verify=settings.VERIFY_SSL) as client:
                response = await client.post(url, files=files, data=data)
                response.raise_for_status()
                payload = response.json()

        segments = [
            TranscriptionSegment(
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                text=seg.get("text", "").strip(),
                confidence=seg.get("avg_logprob", seg.get("confidence", 0.0)),
            )
            for seg in payload.get("segments", [])
        ]

        return TranscriptionResult(
            video_source=str(audio_path),
            language=payload.get("language", "auto"),
            segments=segments,
        )

    async def transcribe(
        self, audio_path: Path, language: str = None
    ) -> TranscriptionResult:
        """
        Transcribes audio file to text using faster-whisper.
        Returns a TranscriptionResult object.
        """
        if settings.ASR_API_URL:
            return await self._transcribe_remote(audio_path, language)

        if self._worker:
            return await self._worker.transcribe(audio_path, language)

        model = self._get_model()

        # transcribe returns a generator of segments
        segments, info = model.transcribe(
            str(audio_path),
            beam_size=settings.ASR_BEAM_SIZE,
            language=language,
            task="transcribe",
            chunk_length=settings.ASR_CHUNK_LENGTH,
        )

        logger.info(
            f"Detected language: {info.language} with probability {info.language_probability:.2f}"
        )

        transcription_segments = []
        for segment in segments:
            transcription_segments.append(
                TranscriptionSegment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text.strip(),
                    confidence=segment.avg_logprob,
                )
            )

        return TranscriptionResult(
            video_source=str(audio_path),
            language=info.language,
            segments=transcription_segments,
        )

    def save_to_json(self, result: TranscriptionResult, filename: str):
        """Saves the transcription result to a JSON file."""
        output_path = settings.OUTPUT_DIR / f"{filename}.json"
        settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result.model_dump_json(indent=4))

        return output_path


from app.core.gpu_lifecycle import gpu_provider_lifecycle
from app.workers.asr_worker import ASRWorkerClient

asr_service = ASRService(
    lifecycle=gpu_provider_lifecycle, worker_factory=ASRWorkerClient
)
