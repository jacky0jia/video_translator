"""Application-private boundary for the pinned ``llama-tts`` executable."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.services.tts.base import TTSCancelled
from app.services.tts.qwen_voices import QwenVoiceCatalog


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGES = frozenset({"zh", "en", "de", "it", "pt", "es", "ja", "ko", "fr", "ru"})
logger = logging.getLogger(__name__)


class LlamaTTSArtifactError(RuntimeError):
    """The private runtime or model failed its integrity checks."""


class LlamaTTSExecutionError(RuntimeError):
    """The verified executable could not start or complete synthesis."""


def select_amd_vulkan_device(artifacts: "LlamaTTSArtifactSet") -> str:
    """Enumerate the attested runtime and select one unambiguous AMD device."""
    environment = os.environ.copy()
    environment["PATH"] = str(artifacts.runtime_root) + os.pathsep + environment.get("PATH", "")
    creation_flags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [str(artifacts.executable), "--list-devices"],
            cwd=artifacts.runtime_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LlamaTTSArtifactError("Unable to enumerate Vulkan devices") from exc
    output = result.stdout + "\n" + result.stderr
    devices = re.findall(r"^\s*(Vulkan\d+):\s*(.+)$", output, re.MULTILINE)
    amd_devices = [
        (identifier, description.strip()) for identifier, description in devices
        if "AMD" in description.upper() or "RADEON" in description.upper()
    ]
    if result.returncode != 0 or len(amd_devices) != 1:
        raise LlamaTTSArtifactError("Expected exactly one AMD Vulkan device")
    identifier, description = amd_devices[0]
    logger.info("Qwen3-TTS selected Vulkan device: %s / %s", identifier, description)
    return identifier


@dataclass(frozen=True)
class LlamaTTSArtifactSet:
    runtime_root: Path
    executable: Path
    model: Path
    mmproj: Path
    runtime_version: str

    @classmethod
    def load(cls, config: dict) -> "LlamaTTSArtifactSet":
        if not isinstance(config, dict):
            raise LlamaTTSArtifactError("llama-tts worker configuration must be an object")
        try:
            runtime_root = Path(config["runtime_root"]).resolve(strict=True)
            manifest_path = Path(config["runtime_manifest"]).resolve(strict=True)
        except (KeyError, OSError, TypeError) as exc:
            raise LlamaTTSArtifactError("Private llama-tts runtime is unavailable") from exc
        try:
            manifest_path.relative_to(runtime_root)
        except ValueError as exc:
            raise LlamaTTSArtifactError("llama-tts manifest escapes its runtime root") from exc
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LlamaTTSArtifactError("llama-tts manifest is unreadable") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise LlamaTTSArtifactError("Unsupported llama-tts manifest schema")
        version = manifest.get("runtime_version")
        executable_name = manifest.get("executable")
        files = manifest.get("files")
        if not isinstance(version, str) or not version.strip():
            raise LlamaTTSArtifactError("llama-tts manifest has no runtime version")
        if executable_name != "llama-tts.exe" and os.name == "nt":
            raise LlamaTTSArtifactError("Private Windows runtime must use llama-tts.exe")
        if not isinstance(executable_name, str) or Path(executable_name).name != executable_name:
            raise LlamaTTSArtifactError("Invalid llama-tts executable path")
        if not isinstance(files, dict) or executable_name not in files:
            raise LlamaTTSArtifactError("llama-tts manifest does not attest its executable")

        for relative_name, expected_hash in files.items():
            cls._verify_direct_file(runtime_root, relative_name, expected_hash, "runtime")
        executable = (runtime_root / executable_name).resolve(strict=True)

        model = cls._verify_configured_file(config, "model_path", "model_sha256", "model")
        mmproj = cls._verify_configured_file(config, "mmproj_path", "mmproj_sha256", "mmproj")
        return cls(runtime_root, executable, model, mmproj, version.strip())

    @classmethod
    def _verify_direct_file(
        cls, root: Path, relative_name: object, expected_hash: object, label: str
    ) -> Path:
        if not isinstance(relative_name, str) or Path(relative_name).name != relative_name:
            raise LlamaTTSArtifactError(f"Invalid llama-tts {label} artifact path")
        cls._validate_hash(expected_hash, label)
        try:
            path = (root / relative_name).resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError) as exc:
            raise LlamaTTSArtifactError(f"llama-tts {label} artifact escapes its root") from exc
        if not path.is_file() or cls._sha256(path) != expected_hash:
            raise LlamaTTSArtifactError(f"llama-tts {label} checksum mismatch: {relative_name}")
        return path

    @classmethod
    def _verify_configured_file(
        cls, config: dict, path_key: str, hash_key: str, label: str
    ) -> Path:
        expected_hash = config.get(hash_key)
        cls._validate_hash(expected_hash, label)
        try:
            path = Path(config[path_key]).resolve(strict=True)
        except (KeyError, OSError, TypeError) as exc:
            raise LlamaTTSArtifactError(f"Qwen3-TTS {label} is unavailable") from exc
        if not path.is_file() or cls._sha256(path) != expected_hash:
            raise LlamaTTSArtifactError(f"Qwen3-TTS {label} checksum mismatch")
        return path

    @staticmethod
    def _validate_hash(value: object, label: str) -> None:
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise LlamaTTSArtifactError(f"Invalid {label} SHA-256")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


class LlamaTTSWorkerClient:
    """Run one pinned llama-tts child at a time with bounded cancellation.

    llama-tts currently has no persistent synthesis protocol, so each request is
    a fresh process.  This owner still forms the private worker boundary: callers
    cannot provide command-line arguments, URLs, model paths or output paths.
    """

    def __init__(
        self,
        *,
        request_timeout: float = 180,
        poll_interval: float = 0.05,
        process_factory=None,
        status_callback=None,
        vulkan_device_selector=None,
    ):
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval
        self._process_factory = process_factory or asyncio.create_subprocess_exec
        self._artifacts: LlamaTTSArtifactSet | None = None
        self._voices: QwenVoiceCatalog | None = None
        self._device = "none"
        self._device_id = None
        self._lock = asyncio.Lock()
        self._process = None
        self._auto_cpu_fallback = True
        self._status_callback = status_callback
        self._vulkan_device_selector = vulkan_device_selector or select_amd_vulkan_device
        self.status = {"actual_device": None, "state": "idle", "fallback_reason": None}

    def _report(self, **updates):
        self.status.update(updates)
        if self._status_callback:
            self._status_callback(dict(self.status))

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self, worker_config: dict) -> None:
        if self._artifacts is not None:
            raise RuntimeError("llama-tts worker is already configured")
        artifacts = await asyncio.to_thread(LlamaTTSArtifactSet.load, worker_config)
        try:
            voices = await asyncio.to_thread(
                QwenVoiceCatalog, worker_config["voice_catalog_root"]
            )
        except (KeyError, OSError, TypeError) as exc:
            raise LlamaTTSArtifactError("Qwen preset voice catalog is unavailable") from exc
        device = worker_config.get("device", "cuda")
        if device not in {"cuda", "vulkan", "cpu"}:
            raise LlamaTTSArtifactError("llama-tts device must be cuda, vulkan or cpu")
        self._auto_cpu_fallback = worker_config.get("auto_cpu_fallback", True)
        fallback_reason = None
        if device == "vulkan":
            try:
                self._device_id = await asyncio.to_thread(self._vulkan_device_selector, artifacts)
            except LlamaTTSArtifactError as exc:
                if not self._auto_cpu_fallback:
                    raise
                device = "cpu"
                fallback_reason = str(exc)
        self._artifacts, self._voices, self._device = artifacts, voices, device
        self._report(
            actual_device=None,
            requested_device=worker_config.get("device", "cuda"),
            state="ready",
            fallback_reason=fallback_reason,
        )

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        language: str,
        should_cancel: Callable[[], bool] | None = None,
        timeout: float | None = None,
    ) -> bytes:
        normalized_text = " ".join(str(text).split())
        normalized_language = str(language).strip().lower()
        if not normalized_text:
            raise ValueError("llama-tts synthesis text must not be empty")
        if len(normalized_text) > 2_000:
            raise ValueError("llama-tts synthesis text exceeds 2000 characters")
        if normalized_language not in _LANGUAGES:
            raise ValueError(f"Unsupported llama-tts language: {normalized_language}")
        if self._artifacts is None or self._voices is None:
            raise RuntimeError("llama-tts worker is not configured")
        voice = self._voices.resolve(voice_id, normalized_language)

        async with self._lock:
            try:
                return await self._attempt(normalized_text, normalized_language, voice, should_cancel, timeout)
            except LlamaTTSExecutionError as exc:
                if self._device not in {"cuda", "vulkan"} or not self._auto_cpu_fallback:
                    raise
                if should_cancel and should_cancel():
                    self._report(state="cancelled")
                    raise TTSCancelled("llama-tts synthesis was cancelled")
                failed_device = self._device
                self._device = "cpu"
                self._device_id = None
                self._report(fallback_reason=str(exc), state="retrying")
                try:
                    return await self._attempt(normalized_text, normalized_language, voice, should_cancel, timeout)
                except LlamaTTSExecutionError as cpu_error:
                    label = "CUDA" if failed_device == "cuda" else "Vulkan"
                    raise LlamaTTSExecutionError(f"{label} failed: {exc}; CPU retry failed: {cpu_error}") from cpu_error

    async def _attempt(self, normalized_text, normalized_language, voice, should_cancel, timeout):
        if should_cancel and should_cancel():
            self._report(state="cancelled")
            raise TTSCancelled("llama-tts synthesis was cancelled")
        self._report(actual_device=self._device, state="running")
        with tempfile.TemporaryDirectory(prefix="subtitle-translator-llama-tts-") as temp:
            output = Path(temp) / "result.wav"
            arguments = self._arguments(
                normalized_text, normalized_language, voice.reference_path, output
            )
            environment = os.environ.copy()
            environment["PATH"] = (
                str(self._artifacts.runtime_root)
                + os.pathsep
                + environment.get("PATH", "")
            )
            creation_flags = 0x08000000 if os.name == "nt" else 0
            try:
                process = await self._process_factory(
                    *arguments,
                    cwd=temp,
                    env=environment,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=creation_flags,
                )
            except asyncio.CancelledError:
                self._report(state="cancelled")
                raise
            except OSError as exc:
                self._report(state="failed")
                raise LlamaTTSExecutionError(f"llama-tts could not start (OS error {exc.errno})") from exc
            self._process = process
            try:
                await self._wait(process, timeout or self.request_timeout, should_cancel)
                if process.returncode != 0:
                    raise LlamaTTSExecutionError(
                        f"llama-tts exited with code {process.returncode}"
                    )
                try:
                    audio = await asyncio.to_thread(self._read_valid_wav, output, normalized_text)
                except RuntimeError as exc:
                    raise LlamaTTSExecutionError(str(exc)) from exc
                self._report(state="succeeded")
                return audio
            except (TTSCancelled, asyncio.CancelledError):
                self._report(state="cancelled")
                raise
            except Exception:
                self._report(state="failed")
                raise
            finally:
                await self._terminate(process)
                self._process = None

    def _arguments(
        self, text: str, language: str, reference: Path, output: Path
    ) -> tuple[str, ...]:
        assert self._artifacts is not None
        arguments = [
            str(self._artifacts.executable),
            "-m",
            str(self._artifacts.model),
            "-mm",
            str(self._artifacts.mmproj),
        ]
        if self._device == "cuda":
            arguments.extend(("-dev", "CUDA0", "-ngl", "all", "-mmdev", "CUDA0"))
        elif self._device == "vulkan":
            if not self._device_id:
                raise LlamaTTSArtifactError("AMD Vulkan device was not selected")
            arguments.extend(("-dev", self._device_id, "-ngl", "all", "-mmdev", self._device_id))
        else:
            arguments.extend(
                ("-dev", "none", "-ngl", "0", "-mmdev", "none", "--no-mmproj-offload", "--no-op-offload")
            )
        arguments.extend(
            (
                "--tts-speaker-file",
                str(reference),
                "--tts-lang",
                language,
                "-p",
                text,
                "-n",
                "256",
                "-o",
                str(output),
            )
        )
        return tuple(arguments)

    async def _wait(
        self,
        process,
        timeout: float,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        deadline = time.monotonic() + timeout
        while process.returncode is None:
            if should_cancel and should_cancel():
                raise TTSCancelled("llama-tts synthesis was cancelled; process recycled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"llama-tts synthesis timed out after {timeout:g}s")
            try:
                await asyncio.wait_for(process.wait(), min(self.poll_interval, remaining))
            except asyncio.TimeoutError:
                pass

    @staticmethod
    async def _terminate(process) -> None:
        if process.returncode is not None:
            return
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), 10)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("llama-tts process could not be reaped") from exc

    @staticmethod
    def _read_valid_wav(path: Path, text: str) -> bytes:
        try:
            if path.stat().st_size > 100 * 1024 * 1024:
                raise RuntimeError("llama-tts output exceeds 100 MiB")
            audio = path.read_bytes()
            with wave.open(str(path), "rb") as source:
                channels = source.getnchannels()
                width = source.getsampwidth()
                rate = source.getframerate()
                frames = source.getnframes()
        except (OSError, EOFError, wave.Error) as exc:
            raise RuntimeError("llama-tts returned an invalid WAV") from exc
        duration = frames / rate if rate else 0
        if channels != 1 or width != 2 or rate != 24_000 or duration < 0.1:
            raise RuntimeError("llama-tts WAV must be non-empty mono PCM-16 at 24000 Hz")
        if duration > max(30.0, len(text) * 1.5):
            raise RuntimeError(f"llama-tts returned abnormal duration: {duration:.3f}s")
        return audio

    async def close(self) -> None:
        process = self._process
        if process is not None:
            await self._terminate(process)
        self._process = None
        self._artifacts = None
        self._voices = None
        self._device_id = None
