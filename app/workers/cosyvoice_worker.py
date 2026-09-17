"""Offline CosyVoice worker boundary and artifact validation.

This module is intentionally not registered as a production TTS provider.  It
defines the process and supply-chain boundary that a future provider may use.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import multiprocessing
import os
import time
import traceback
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from app.services.tts.base import TTSCancelled


REQUIRED_MODEL_FILES = frozenset(
    {
        "campplus.onnx",
        "flow.decoder.estimator.fp16.onnx",
        "flow_pre_lookahead_fp16.onnx",
        "flow_speaker_projection_fp16.onnx",
        "flow_token_embedding_fp16.onnx",
        "hift_decoder_fp32.onnx",
        "hift_f0_predictor_fp32.onnx",
        "hift_source_generator_fp32.onnx",
        "llm_backbone_decode_fp16.onnx",
        "llm_backbone_initial_fp16.onnx",
        "llm_decoder_fp16.onnx",
        "llm_speech_embedding_fp16.onnx",
        "speech_tokenizer_v3.onnx",
        "text_embedding_fp32.onnx",
        "merges.txt",
        "tokenizer_config.json",
        "vocab.json",
    }
)


class CosyVoiceArtifactError(RuntimeError):
    """The offline model bundle failed its integrity or policy checks."""


PRIVATE_GPU_RUNTIME_VERSION = "1.18.0"
PRIVATE_GPU_REQUIRED_FILES = frozenset(
    {
        "cublas64_12.dll",
        "cublasLt64_12.dll",
        "cudart64_12.dll",
        "cudnn_adv_infer64_8.dll",
        "cudnn_cnn_infer64_8.dll",
        "cudnn_ops_infer64_8.dll",
        "cudnn64_8.dll",
        "cufft64_11.dll",
        "zlibwapi.dll",
    }
)


def _prepare_onnx_execution_provider(config: dict):
    """Prepare and attest the explicitly selected ONNX execution provider.

    The NVIDIA runtime is application-private: only its child process receives
    the DLL search path.  A requested GPU must never silently degrade to CPU.
    The returned DLL-directory handle must stay alive for the worker lifetime.
    """
    requested = config.get("execution_provider", "cpu")
    if requested == "cpu":
        return "CPUExecutionProvider", None
    if requested != "nvidia_cuda":
        raise CosyVoiceArtifactError(
            f"Unsupported CosyVoice execution provider: {requested}"
        )

    runtime_root = Path(config.get("gpu_runtime_root", "")).resolve(strict=True)
    manifest_path = Path(config.get("gpu_runtime_manifest", "")).resolve(strict=True)
    try:
        manifest_path.relative_to(runtime_root)
    except ValueError as exc:
        raise CosyVoiceArtifactError("GPU runtime manifest escapes its private root") from exc
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CosyVoiceArtifactError("GPU runtime manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise CosyVoiceArtifactError("Unsupported GPU runtime manifest schema")
    if (
        manifest.get("onnxruntime_version") != PRIVATE_GPU_RUNTIME_VERSION
        or manifest.get("cuda_major") != 12
        or manifest.get("cudnn_major") != 8
    ):
        raise CosyVoiceArtifactError(
            "CosyVoice GPU runtime must use ONNX Runtime 1.18.0, CUDA 12 and cuDNN 8"
        )
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict) or not PRIVATE_GPU_REQUIRED_FILES.issubset(raw_files):
        raise CosyVoiceArtifactError("GPU runtime manifest is missing required cuDNN 8 files")
    for filename, expected_hash in raw_files.items():
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise CosyVoiceArtifactError(f"Invalid GPU runtime artifact path: {filename}")
        if not filename.lower().endswith(".dll") or "cudnn64_9" in filename.lower():
            raise CosyVoiceArtifactError(f"Forbidden GPU runtime artifact: {filename}")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(
            char not in "0123456789abcdef" for char in expected_hash
        ):
            raise CosyVoiceArtifactError(f"Invalid GPU runtime artifact hash: {filename}")
        artifact = (runtime_root / filename).resolve(strict=True)
        try:
            artifact.relative_to(runtime_root)
        except ValueError as exc:
            raise CosyVoiceArtifactError(
                f"GPU runtime artifact escapes its private root: {filename}"
            ) from exc
        if not artifact.is_file() or CosyVoiceArtifactSet._sha256(artifact) != expected_hash:
            raise CosyVoiceArtifactError(f"GPU runtime checksum mismatch: {filename}")

    os.environ["PATH"] = str(runtime_root) + os.pathsep + os.environ.get("PATH", "")
    dll_handle = os.add_dll_directory(str(runtime_root)) if os.name == "nt" else None
    import onnxruntime as ort

    if ort.__version__ != PRIVATE_GPU_RUNTIME_VERSION:
        raise CosyVoiceArtifactError(
            f"Private GPU worker loaded ONNX Runtime {ort.__version__}; expected 1.18.0"
        )
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise CosyVoiceArtifactError(
            "Private NVIDIA runtime did not expose CUDAExecutionProvider"
        )
    return "CUDAExecutionProvider", dll_handle


@dataclass(frozen=True)
class CosyVoicePreset:
    id: str
    reference_path: Path
    reference_text: str
    sha256: str


def _validate_presets(root: str | Path, values: object) -> dict[str, CosyVoicePreset]:
    reference_root = Path(root).resolve(strict=True)
    if not isinstance(values, list) or not values:
        raise CosyVoiceArtifactError("CosyVoice preset manifest contains no voices")
    presets: dict[str, CosyVoicePreset] = {}
    for value in values:
        if not isinstance(value, dict):
            raise CosyVoiceArtifactError("Invalid CosyVoice preset entry")
        voice_id = value.get("id")
        filename = value.get("file")
        text = value.get("reference_text")
        expected_hash = value.get("sha256")
        if not isinstance(voice_id, str) or not voice_id or Path(voice_id).name != voice_id:
            raise CosyVoiceArtifactError("Invalid CosyVoice preset id")
        if voice_id in presets:
            raise CosyVoiceArtifactError(f"Duplicate CosyVoice preset id: {voice_id}")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise CosyVoiceArtifactError(f"Invalid CosyVoice preset filename: {voice_id}")
        if not isinstance(text, str) or not text.strip():
            raise CosyVoiceArtifactError(f"Missing CosyVoice preset transcript: {voice_id}")
        if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(
            char not in "0123456789abcdef" for char in expected_hash
        ):
            raise CosyVoiceArtifactError(f"Invalid CosyVoice preset hash: {voice_id}")
        path = (reference_root / filename).resolve(strict=True)
        try:
            path.relative_to(reference_root)
        except ValueError as exc:
            raise CosyVoiceArtifactError(
                f"CosyVoice preset escapes the reference root: {voice_id}"
            ) from exc
        if CosyVoiceArtifactSet._sha256(path) != expected_hash:
            raise CosyVoiceArtifactError(f"CosyVoice preset checksum mismatch: {voice_id}")
        try:
            with wave.open(str(path), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
                valid = (
                    audio.getnchannels() == 1
                    and audio.getsampwidth() == 2
                    and audio.getframerate() == 24_000
                    and 3.0 <= duration <= 10.0
                )
        except (OSError, EOFError, wave.Error) as exc:
            raise CosyVoiceArtifactError(f"Invalid CosyVoice preset WAV: {voice_id}") from exc
        if not valid:
            raise CosyVoiceArtifactError(
                f"CosyVoice preset must be mono PCM-16 at 24000 Hz and 3-10 seconds: {voice_id}"
            )
        presets[voice_id] = CosyVoicePreset(
            id=voice_id,
            reference_path=path,
            reference_text=" ".join(text.split()),
            sha256=expected_hash,
        )
    return presets


@dataclass(frozen=True)
class CosyVoiceArtifactSet:
    root: Path
    model_id: str
    revision: str
    files: dict[str, str]

    @classmethod
    def load(cls, root: str | Path, manifest_path: str | Path) -> "CosyVoiceArtifactSet":
        bundle_root = Path(root).resolve(strict=True)
        manifest_file = Path(manifest_path).resolve(strict=True)
        try:
            manifest_file.relative_to(bundle_root)
        except ValueError as exc:
            raise CosyVoiceArtifactError("CosyVoice manifest escapes the bundle root") from exc
        try:
            value = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CosyVoiceArtifactError("CosyVoice manifest is unreadable") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise CosyVoiceArtifactError("Unsupported CosyVoice manifest schema")
        model_id = value.get("model_id")
        revision = value.get("revision")
        raw_files = value.get("files")
        if not isinstance(model_id, str) or not model_id.strip():
            raise CosyVoiceArtifactError("CosyVoice manifest has no model id")
        if not isinstance(revision, str) or len(revision) != 40 or any(
            char not in "0123456789abcdef" for char in revision
        ):
            raise CosyVoiceArtifactError("CosyVoice manifest revision must be a full commit hash")
        if not isinstance(raw_files, dict) or set(raw_files) != REQUIRED_MODEL_FILES:
            missing = sorted(REQUIRED_MODEL_FILES.difference(raw_files or {}))
            extra = sorted(set(raw_files or {}).difference(REQUIRED_MODEL_FILES))
            raise CosyVoiceArtifactError(
                f"CosyVoice manifest file set mismatch (missing={missing}, extra={extra})"
            )

        verified: dict[str, str] = {}
        for relative_name, expected_hash in raw_files.items():
            if Path(relative_name).name != relative_name:
                raise CosyVoiceArtifactError(f"Invalid CosyVoice artifact path: {relative_name}")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(
                char not in "0123456789abcdef" for char in expected_hash
            ):
                raise CosyVoiceArtifactError(f"Invalid CosyVoice artifact hash: {relative_name}")
            artifact = (bundle_root / relative_name).resolve(strict=True)
            try:
                artifact.relative_to(bundle_root)
            except ValueError as exc:
                raise CosyVoiceArtifactError(
                    f"CosyVoice artifact escapes the bundle root: {relative_name}"
                ) from exc
            if not artifact.is_file() or cls._sha256(artifact) != expected_hash:
                raise CosyVoiceArtifactError(
                    f"CosyVoice artifact checksum mismatch: {relative_name}"
                )
            verified[relative_name] = expected_hash

        cls._validate_tokenizer_policy(bundle_root / "tokenizer_config.json")
        return cls(bundle_root, model_id.strip(), revision, verified)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _validate_tokenizer_policy(path: Path) -> None:
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CosyVoiceArtifactError("CosyVoice tokenizer configuration is unreadable") from exc
        if not isinstance(config, dict):
            raise CosyVoiceArtifactError("CosyVoice tokenizer configuration must be an object")
        if config.get("auto_map") or config.get("custom_pipelines"):
            raise CosyVoiceArtifactError("CosyVoice tokenizer remote/custom code is forbidden")


def _load_offline_runtime_class(
    script_path: Path,
    expected_hash: str,
    *,
    cpu_session_names: tuple[str, ...] = (),
):
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        raise CosyVoiceArtifactError("Invalid CosyVoice runtime script hash")
    resolved = script_path.resolve(strict=True)
    if CosyVoiceArtifactSet._sha256(resolved) != expected_hash:
        raise CosyVoiceArtifactError("CosyVoice runtime script checksum mismatch")
    spec = importlib.util.spec_from_file_location("_pinned_cosyvoice_onnx_runtime", resolved)
    if spec is None or spec.loader is None:
        raise CosyVoiceArtifactError("CosyVoice runtime script cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    upstream = getattr(module, "PureOnnxCosyVoice3", None)
    if not isinstance(upstream, type):
        raise CosyVoiceArtifactError("CosyVoice runtime class was not found")

    class FiniteOutputSession:
        def __init__(self, name, session):
            self._name = name
            self._session = session

        def __getattr__(self, name):
            return getattr(self._session, name)

        def run(self, *args, **kwargs):
            outputs = self._session.run(*args, **kwargs)
            for output in outputs:
                value = np.asarray(output)
                if np.issubdtype(value.dtype, np.floating) and not np.isfinite(value).all():
                    raise RuntimeError(
                        f"CosyVoice session produced non-finite output: {self._name}"
                    )
            return outputs

    class OfflineCosyVoiceRuntime(upstream):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if cpu_session_names:
                import onnxruntime as ort

                for name in cpu_session_names:
                    filename = {
                        "llm_backbone_initial": "llm_backbone_initial_fp16.onnx",
                        "llm_backbone_decode": "llm_backbone_decode_fp16.onnx",
                        "flow_estimator": "flow.decoder.estimator.fp16.onnx",
                    }[name]
                    model_path = Path(self.onnx_dir) / filename
                    setattr(
                        self,
                        name,
                        ort.InferenceSession(
                            str(model_path), providers=["CPUExecutionProvider"]
                        ),
                    )
            for name in RUNTIME_SESSION_NAMES:
                setattr(self, name, FiniteOutputSession(name, getattr(self, name)))

        def _load_tokenizer(self):
            from transformers import AutoTokenizer

            self.tokenizer = AutoTokenizer.from_pretrained(
                self.onnx_dir,
                local_files_only=True,
                trust_remote_code=False,
            )

    return OfflineCosyVoiceRuntime


RUNTIME_SESSION_NAMES = (
    "text_embedding",
    "campplus",
    "speech_tokenizer",
    "llm_backbone_initial",
    "llm_backbone_decode",
    "llm_decoder",
    "llm_speech_embedding",
    "flow_token_embedding",
    "flow_speaker_projection",
    "flow_pre_lookahead",
    "flow_estimator",
    "hift_f0_predictor",
    "hift_source_generator",
    "hift_decoder",
)
GPU_CPU_FALLBACK_SESSIONS = (
    # The reviewed FP16 export is not numerically safe for these CUDA graphs.
    # Keeping this explicit prevents invalid audio and upstream's silent CPU
    # fallback, but benchmarks show this hybrid route is not a speedup.
    "llm_backbone_initial",
    "llm_backbone_decode",
    "flow_estimator",
)


def _attest_runtime_sessions(engine, execution_provider: str) -> None:
    """Reject upstream's silent CUDA-to-CPU fallback after real model loading."""
    for name in RUNTIME_SESSION_NAMES:
        session = getattr(engine, name, None)
        if session is None or not callable(getattr(session, "get_providers", None)):
            raise CosyVoiceArtifactError(f"CosyVoice runtime session is missing: {name}")
        providers = session.get_providers()
        expected = (
            "CPUExecutionProvider"
            if execution_provider == "CPUExecutionProvider"
            or name in GPU_CPU_FALLBACK_SESSIONS
            else "CUDAExecutionProvider"
        )
        if expected not in providers or providers[0] != expected:
            raise CosyVoiceArtifactError(
                f"CosyVoice session {name} did not activate {expected}: {providers}"
            )


def _resolve_verified_model_dir(
    model_dir: str | Path, artifacts: CosyVoiceArtifactSet
) -> Path:
    resolved = Path(model_dir).resolve(strict=True)
    try:
        loaded_artifact_root = (resolved / "onnx").resolve(strict=True)
    except OSError as exc:
        raise CosyVoiceArtifactError("CosyVoice model directory has no ONNX bundle") from exc
    if loaded_artifact_root != artifacts.root:
        raise CosyVoiceArtifactError(
            "CosyVoice model directory does not reference the verified artifact bundle"
        )
    return resolved


def cosyvoice_worker_main(connection, config):
    """Load a pinned community ONNX runtime behind application-owned policy."""
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        execution_provider, gpu_dll_handle = _prepare_onnx_execution_provider(config)
        artifacts = CosyVoiceArtifactSet.load(
            config["artifact_root"], config["artifact_manifest"]
        )
        model_dir = _resolve_verified_model_dir(config["model_dir"], artifacts)
        presets = _validate_presets(config["reference_root"], config["presets"])
        runtime_class = _load_offline_runtime_class(
            Path(config["runtime_script"]),
            config["runtime_script_sha256"],
            cpu_session_names=(
                GPU_CPU_FALLBACK_SESSIONS
                if execution_provider == "CUDAExecutionProvider"
                else ()
            ),
        )
        engine = runtime_class(str(model_dir), use_fp16=True)
        _attest_runtime_sessions(engine, execution_provider)
        connection.send(
            {"ok": True, "type": "ready", "execution_provider": execution_provider}
        )
        while True:
            command = connection.recv()
            if command.get("type") == "shutdown":
                break
            if command.get("type") != "synthesize":
                raise RuntimeError(f"Unknown CosyVoice worker command: {command.get('type')}")
            voice_id = command.get("voice_id")
            text = command.get("text")
            if voice_id not in presets or not isinstance(text, str) or not text.strip():
                raise ValueError("Invalid CosyVoice synthesis request")
            preset = presets[voice_id]
            audio = np.asarray(
                engine.inference(
                    text.strip(),
                    prompt_wav=str(preset.reference_path),
                    prompt_text=preset.reference_text,
                ),
                dtype=np.float32,
            ).reshape(-1)
            if audio.size == 0 or not np.isfinite(audio).all():
                raise RuntimeError("CosyVoice produced invalid audio")
            duration = audio.size / 24_000
            if duration < 0.1 or duration > max(30.0, len(text) * 1.5):
                raise RuntimeError(f"CosyVoice produced abnormal duration: {duration:.3f}s")
            output = io.BytesIO()
            import soundfile as sf

            sf.write(output, audio, 24_000, format="WAV", subtype="PCM_16")
            connection.send({"ok": True, "type": "result", "audio": output.getvalue()})
    except BaseException as exc:
        try:
            connection.send(
                {
                    "ok": False,
                    "type": "error",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        except Exception:
            pass
    finally:
        connection.close()


class CosyVoiceWorkerClient:
    """Serialized client for a persistent, isolated synthesis worker.

    Cancellation and request timeout deliberately recycle the worker process;
    the reviewed ONNX runtime has no cooperative mid-inference cancellation.
    """

    def __init__(
        self,
        *,
        startup_timeout: float = 120,
        request_timeout: float = 600,
        shutdown_timeout: float = 10,
        poll_interval: float = 0.1,
        worker_target=None,
    ):
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.shutdown_timeout = shutdown_timeout
        self.poll_interval = poll_interval
        self._worker_target = worker_target or cosyvoice_worker_main
        self._process = None
        self._connection = None
        self._request_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.is_alive())

    async def start(self, worker_config: dict) -> None:
        if self.running:
            raise RuntimeError("CosyVoice worker is already running")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=self._worker_target,
            args=(child, worker_config),
            name="subtitle-translator-cosyvoice",
            daemon=True,
        )
        process.start()
        child.close()
        self._process, self._connection = process, parent
        try:
            response = await self._receive(self.startup_timeout)
        except BaseException:
            await self.close()
            raise
        if not response.get("ok") or response.get("type") != "ready":
            await self.close()
            raise RuntimeError(f"CosyVoice worker startup failed: {response.get('error')}")

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        should_cancel: Callable[[], bool] | None = None,
        timeout: float | None = None,
    ) -> bytes:
        if not text.strip():
            raise ValueError("CosyVoice synthesis text must not be empty")
        if not voice_id or Path(voice_id).name != voice_id:
            raise ValueError("CosyVoice voice id must be one path component")
        async with self._request_lock:
            if not self.running or self._connection is None:
                raise RuntimeError("CosyVoice worker is not running")
            self._connection.send({"type": "synthesize", "text": text, "voice_id": voice_id})
            try:
                response = await self._receive_interruptible(
                    timeout or self.request_timeout, should_cancel
                )
            except (TTSCancelled, TimeoutError):
                await self.abort()
                raise
            if not response.get("ok") or response.get("type") != "result":
                raise RuntimeError(f"CosyVoice worker failed: {response.get('error')}")
            audio = response.get("audio")
            if not isinstance(audio, bytes) or not audio:
                raise RuntimeError("CosyVoice worker returned empty audio")
            return audio

    async def _receive_interruptible(
        self, timeout: float, should_cancel: Callable[[], bool] | None
    ) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            if should_cancel and should_cancel():
                raise TTSCancelled("CosyVoice synthesis was cancelled; worker recycled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CosyVoice worker timed out after {timeout:g}s")
            wait = min(self.poll_interval, remaining)
            response = await self._poll_receive(wait)
            if response is not None:
                return response

    async def _receive(self, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CosyVoice worker timed out after {timeout:g}s")
            response = await self._poll_receive(min(self.poll_interval, remaining))
            if response is not None:
                return response

    async def _poll_receive(self, timeout: float) -> dict | None:
        connection = self._connection
        if connection is None:
            raise RuntimeError("CosyVoice worker is not connected")

        def poll_receive():
            if not connection.poll(timeout):
                return None
            try:
                return connection.recv()
            except EOFError as exc:
                code = self._process.exitcode if self._process else None
                raise RuntimeError(
                    f"CosyVoice worker exited unexpectedly (code={code})"
                ) from exc

        return await asyncio.to_thread(poll_receive)

    async def close(self) -> None:
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if not process:
            return
        if process.is_alive() and connection:
            try:
                connection.send({"type": "shutdown"})
            except (BrokenPipeError, EOFError, OSError):
                pass
            await asyncio.to_thread(process.join, self.shutdown_timeout)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, self.shutdown_timeout)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, self.shutdown_timeout)
        if connection:
            connection.close()
        process.close()

    async def abort(self) -> None:
        """Immediately recycle a worker that is busy or no longer trustworthy."""
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if not process:
            return
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, self.shutdown_timeout)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, self.shutdown_timeout)
        if connection:
            connection.close()
        process.close()
