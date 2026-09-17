import logging
import os
import platform
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from app.core.config_migrations import CURRENT_CONFIG_VERSION, migrate_config
from app.core.settings_schema import (
    EDITABLE_SETTING_KEYS,
    MASKED_SECRET,
    SETTINGS_BY_KEY,
    normalize_setting_value,
)

logger = logging.getLogger(__name__)


class Settings:
    # Immutable defaults
    PROJECT_NAME: str = "Video Translator"
    UI_LANGUAGE: str = ""
    CONFIG_VERSION: int = CURRENT_CONFIG_VERSION

    # Base directories
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    TEMP_DIR: Path = BASE_DIR / "temp"
    OUTPUT_DIR: Path = BASE_DIR / "output"

    # LLM Configuration defaults
    LLM_API_BASE_URL: str = "http://127.0.0.1:8001/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = ""
    LLM_TEMPERATURE: float = 0.3
    LLM_TIMEOUT_SECONDS: float = 180.0
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BACKOFF_BASE: int = 3
    TRANSLATION_BATCH_SIZE: int = 10
    LLM_PROVIDER: str = "lm_studio"
    LM_STUDIO_BASE_URL: str = "http://127.0.0.1:1234/v1"
    LM_STUDIO_MODEL: str = ""
    LM_STUDIO_CLI_PATH: str = "lms"
    LM_STUDIO_PORT: int = 1234
    LM_STUDIO_TTL_SECONDS: int = 300
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = ""
    OLLAMA_KEEP_ALIVE: str = "5m"

    # SSL verification (disable for self-signed certs on local LLM/ASR/TTS endpoints)
    VERIFY_SSL: bool = True

    # ASR Model Configuration defaults
    ASR_MODEL_SIZE: str = "base"
    ASR_MODEL_PATH: Optional[str] = None
    ASR_BEAM_SIZE: int = 5
    ASR_CHUNK_LENGTH: int = 6  # faster-whisper audio chunk length in seconds
    ASR_API_URL: Optional[str] = None  # e.g. http://127.0.0.1:8001/asr
    ASR_REMOTE_MODEL: Optional[str] = None  # e.g. Systran/faster-whisper-small

    # FFmpeg Configuration
    FFMPEG_BIN_DIR: Path = BASE_DIR / "bin"
    FFMPEG_PATH: Optional[str] = None  # Manual override; if None, use auto-detection

    # Device & Compute defaults
    DEVICE_PREFERENCE: str = "auto"  # auto | gpu | cpu
    COMPUTE_TYPE: str = "auto"  # auto | float16 | int8

    # Chunking defaults
    CHUNKING_THRESHOLD_MINUTES: int = 15
    CHUNK_OVERLAP_SECONDS: int = 3

    # Audio processing defaults
    AUDIO_SAMPLE_RATE: int = 16000
    AUDIO_CHANNELS: int = 1
    DEDUPLICATE_THRESHOLD_SECONDS: float = 0.5

    # Subtitle style defaults (ASS)
    ASS_PLAYRES_X: int = 640
    ASS_PLAYRES_Y: int = 360
    ASS_FONT_NAME: str = "Arial"
    ASS_FONT_SIZE_ORIGINAL: int = 18
    ASS_FONT_SIZE_TRANSLATED: int = 20

    # SSE / Event defaults
    SSE_QUEUE_MAXSIZE: int = 100
    SSE_KEEPALIVE_TIMEOUT: float = 30.0

    # TTS / Dubbing Configuration defaults
    TTS_MODE: str = "kokoro"  # kokoro | qwen | cosyvoice | edge | speaches
    QWEN_AUTO_CPU_FALLBACK: bool = True
    TTS_API_URL: str = "http://localhost:8000"
    TTS_API_KEY: str = ""
    TTS_MODEL: str = "speaches-ai/Kokoro-82M-v1.0-ONNX"
    TTS_DEFAULT_VOICE: str = "af_heart"
    TTS_SPEED: float = 1.0
    DUB_SAMPLE_RATE: int = 22050
    TTS_VOICES_DIR: Optional[str] = None  # Local-only: dir to copy clone samples into
    # Local Kokoro ONNX model paths (empty = auto-download to models/kokoro/)
    KOKORO_MODEL_PATH: str = ""
    KOKORO_VOICES_PATH: str = ""

    # Config file path
    CONFIG_PATH: Path = BASE_DIR.parent / "config.yaml"

    # Compatibility alias. The typed registry is now the source of truth.
    UI_FIELDS: tuple = EDITABLE_SETTING_KEYS

    def __init__(self):
        self._unknown_config: Dict[str, Any] = {}
        self._load_from_file()
        # Ensure directories exist
        for d in (self.UPLOAD_DIR, self.TEMP_DIR, self.OUTPUT_DIR):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def ffmpeg_path(self) -> str:
        # 1. Manual override from config
        if self.FFMPEG_PATH:
            manual = Path(self.FFMPEG_PATH)
            if not manual.is_absolute():
                manual = self.BASE_DIR / manual
            if manual.exists():
                return str(manual)
            logger.warning(f"Configured FFMPEG_PATH not found: {self.FFMPEG_PATH}")

        # 2. Windows bundled binary (try common subdirectories)
        if platform.system() == "Windows":
            for subdir in ("ffmpeg", "bin"):
                bundled_path = self.BASE_DIR / subdir / "ffmpeg.exe"
                if bundled_path.exists():
                    return str(bundled_path)

        # 3. System PATH
        import shutil

        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            return system_ffmpeg

        return ""

    def _load_from_file(self):
        """Overlay a migrated config.yaml on top of hardcoded defaults."""
        if not self.CONFIG_PATH.exists():
            return

        try:
            with open(self.CONFIG_PATH, "r", encoding="utf-8") as f:
                raw: Dict[str, Any] = yaml.safe_load(f) or {}
            if not isinstance(raw, dict):
                raise ValueError("Configuration root must be a mapping.")
            data = migrate_config(raw)
            known_fields = set(self._known_config_data())
            self.CONFIG_VERSION = int(data.pop("CONFIG_VERSION", CURRENT_CONFIG_VERSION))

            for key, value in data.items():
                if key not in known_fields:
                    self._unknown_config[key] = value
                    logger.warning(f"Unknown config key '{key}' in {self.CONFIG_PATH}; preserving it unchanged.")
                    continue
                if key in SETTINGS_BY_KEY:
                    definition = SETTINGS_BY_KEY[key]
                    value = normalize_setting_value(definition, value)
                    if definition.secret and value == MASKED_SECRET:
                        logger.warning(
                            "Discarding persisted mask placeholder for secret field '%s'.",
                            key,
                        )
                        value = ""
                    if definition.format == "url" and value:
                        value = self._validate_url(key, str(value))
                elif key in ("LLM_TEMPERATURE", "LLM_TIMEOUT_SECONDS", "DEDUPLICATE_THRESHOLD_SECONDS", "SSE_KEEPALIVE_TIMEOUT", "TTS_SPEED"):
                    value = float(value)
                elif key in ("LLM_MAX_RETRIES", "LLM_RETRY_BACKOFF_BASE", "TRANSLATION_BATCH_SIZE", "ASR_BEAM_SIZE",
                             "ASR_CHUNK_LENGTH", "CHUNKING_THRESHOLD_MINUTES", "CHUNK_OVERLAP_SECONDS", "AUDIO_SAMPLE_RATE", "AUDIO_CHANNELS",
                             "ASS_PLAYRES_X", "ASS_PLAYRES_Y", "ASS_FONT_SIZE_ORIGINAL", "ASS_FONT_SIZE_TRANSLATED", "SSE_QUEUE_MAXSIZE", "DUB_SAMPLE_RATE",
                             "LM_STUDIO_PORT", "LM_STUDIO_TTL_SECONDS"):
                    value = int(value)
                elif key == "VERIFY_SSL":
                    value = bool(value)
                setattr(self, key, value)

            logger.info(f"Loaded configuration from {self.CONFIG_PATH}")
        except Exception as e:
            logger.error(f"Failed to load config file {self.CONFIG_PATH}: {e}")

    def _known_config_data(self) -> Dict[str, Any]:
        return {
            "UI_LANGUAGE": self.UI_LANGUAGE,
            # LLM
            "LLM_API_BASE_URL": self.LLM_API_BASE_URL,
            "LLM_API_KEY": self.LLM_API_KEY,
            "LLM_MODEL_NAME": self.LLM_MODEL_NAME,
            "LLM_TEMPERATURE": self.LLM_TEMPERATURE,
            "LLM_TIMEOUT_SECONDS": self.LLM_TIMEOUT_SECONDS,
            "LLM_MAX_RETRIES": self.LLM_MAX_RETRIES,
            "LLM_RETRY_BACKOFF_BASE": self.LLM_RETRY_BACKOFF_BASE,
            "TRANSLATION_BATCH_SIZE": self.TRANSLATION_BATCH_SIZE,
            "LLM_PROVIDER": self.LLM_PROVIDER,
            "LM_STUDIO_BASE_URL": self.LM_STUDIO_BASE_URL,
            "LM_STUDIO_MODEL": self.LM_STUDIO_MODEL,
            "LM_STUDIO_CLI_PATH": self.LM_STUDIO_CLI_PATH,
            "LM_STUDIO_PORT": self.LM_STUDIO_PORT,
            "LM_STUDIO_TTL_SECONDS": self.LM_STUDIO_TTL_SECONDS,
            "OLLAMA_BASE_URL": self.OLLAMA_BASE_URL,
            "OLLAMA_MODEL": self.OLLAMA_MODEL,
            "OLLAMA_KEEP_ALIVE": self.OLLAMA_KEEP_ALIVE,
            "VERIFY_SSL": self.VERIFY_SSL,
            # ASR
            "ASR_MODEL_SIZE": self.ASR_MODEL_SIZE,
            "ASR_MODEL_PATH": self.ASR_MODEL_PATH,
            "ASR_BEAM_SIZE": self.ASR_BEAM_SIZE,
            "ASR_CHUNK_LENGTH": self.ASR_CHUNK_LENGTH,
            "ASR_API_URL": self.ASR_API_URL,
            "ASR_REMOTE_MODEL": self.ASR_REMOTE_MODEL,
            # FFmpeg
            "FFMPEG_PATH": self.FFMPEG_PATH,
            # Device
            "DEVICE_PREFERENCE": self.DEVICE_PREFERENCE,
            "COMPUTE_TYPE": self.COMPUTE_TYPE,
            # Chunking
            "CHUNKING_THRESHOLD_MINUTES": self.CHUNKING_THRESHOLD_MINUTES,
            "CHUNK_OVERLAP_SECONDS": self.CHUNK_OVERLAP_SECONDS,
            # Audio
            "AUDIO_SAMPLE_RATE": self.AUDIO_SAMPLE_RATE,
            "AUDIO_CHANNELS": self.AUDIO_CHANNELS,
            "DEDUPLICATE_THRESHOLD_SECONDS": self.DEDUPLICATE_THRESHOLD_SECONDS,
            # ASS Style
            "ASS_PLAYRES_X": self.ASS_PLAYRES_X,
            "ASS_PLAYRES_Y": self.ASS_PLAYRES_Y,
            "ASS_FONT_NAME": self.ASS_FONT_NAME,
            "ASS_FONT_SIZE_ORIGINAL": self.ASS_FONT_SIZE_ORIGINAL,
            "ASS_FONT_SIZE_TRANSLATED": self.ASS_FONT_SIZE_TRANSLATED,
            # SSE
            "SSE_QUEUE_MAXSIZE": self.SSE_QUEUE_MAXSIZE,
            "SSE_KEEPALIVE_TIMEOUT": self.SSE_KEEPALIVE_TIMEOUT,
            # TTS / Dubbing
            "TTS_MODE": self.TTS_MODE,
            "QWEN_AUTO_CPU_FALLBACK": self.QWEN_AUTO_CPU_FALLBACK,
            "TTS_API_URL": self.TTS_API_URL,
            "TTS_API_KEY": self.TTS_API_KEY,
            "TTS_MODEL": self.TTS_MODEL,
            "TTS_DEFAULT_VOICE": self.TTS_DEFAULT_VOICE,
            "TTS_SPEED": self.TTS_SPEED,
            "DUB_SAMPLE_RATE": self.DUB_SAMPLE_RATE,
            "TTS_VOICES_DIR": self.TTS_VOICES_DIR,
            "KOKORO_MODEL_PATH": self.KOKORO_MODEL_PATH,
            "KOKORO_VOICES_PATH": self.KOKORO_VOICES_PATH,
        }

    def save_to_file(self):
        """Persist settings atomically while preserving unknown extension keys."""
        data: Dict[str, Any] = {"CONFIG_VERSION": CURRENT_CONFIG_VERSION}
        data.update(self._unknown_config)
        data.update(self._known_config_data())
        config_dir = self.CONFIG_PATH.parent
        config_dir.mkdir(parents=True, exist_ok=True)
        temp_path: Optional[Path] = None
        try:
            fd, raw_temp_path = tempfile.mkstemp(
                prefix=f".{self.CONFIG_PATH.name}.", suffix=".tmp", dir=str(config_dir)
            )
            temp_path = Path(raw_temp_path)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.CONFIG_PATH)
            temp_path = None
            self.CONFIG_VERSION = CURRENT_CONFIG_VERSION
            logger.info(f"Saved configuration to {self.CONFIG_PATH}")
        except Exception as e:
            logger.error(f"Failed to save config file {self.CONFIG_PATH}: {e}")
            raise
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Failed to remove temporary config file %s", temp_path)

    def _safe_path(self, path: Optional[str]) -> str:
        """Convert absolute path to relative path (relative to BASE_DIR) to avoid exposing directory structure."""
        if not path:
            return ""
        p = Path(path)
        if p.is_absolute():
            try:
                rel = p.relative_to(self.BASE_DIR)
                return str(rel)
            except ValueError:
                # Path is outside BASE_DIR, return filename only
                return p.name
        return path

    def _safe_model_name(self, name: str) -> str:
        """Strip directory from model name if it looks like a path."""
        if not name:
            return name
        if '/' in name or '\\' in name:
            return Path(name).name
        return name

    def to_public_dict(self) -> Dict[str, Any]:
        """Return settings suitable for frontend display (API key masked, paths sanitized)."""
        return {
            "UI_LANGUAGE": self.UI_LANGUAGE,
            # LLM
            "LLM_API_BASE_URL": self.LLM_API_BASE_URL,
            "LLM_API_KEY": "********" if self.LLM_API_KEY else "",
            "LLM_MODEL_NAME": self.LLM_MODEL_NAME,
            "LLM_TEMPERATURE": self.LLM_TEMPERATURE,
            "LLM_TIMEOUT_SECONDS": self.LLM_TIMEOUT_SECONDS,
            "LLM_MAX_RETRIES": self.LLM_MAX_RETRIES,
            "LLM_RETRY_BACKOFF_BASE": self.LLM_RETRY_BACKOFF_BASE,
            "TRANSLATION_BATCH_SIZE": self.TRANSLATION_BATCH_SIZE,
            "LLM_PROVIDER": self.LLM_PROVIDER,
            "LM_STUDIO_BASE_URL": self.LM_STUDIO_BASE_URL,
            "LM_STUDIO_MODEL": self.LM_STUDIO_MODEL,
            "LM_STUDIO_CLI_PATH": self.LM_STUDIO_CLI_PATH,
            "LM_STUDIO_PORT": self.LM_STUDIO_PORT,
            "LM_STUDIO_TTL_SECONDS": self.LM_STUDIO_TTL_SECONDS,
            "OLLAMA_BASE_URL": self.OLLAMA_BASE_URL,
            "OLLAMA_MODEL": self.OLLAMA_MODEL,
            "OLLAMA_KEEP_ALIVE": self.OLLAMA_KEEP_ALIVE,
            # ASR
            "ASR_MODEL_SIZE": self.ASR_MODEL_SIZE,
            "ASR_MODEL_PATH": self.ASR_MODEL_PATH or "",
            "ASR_BEAM_SIZE": self.ASR_BEAM_SIZE,
            "ASR_CHUNK_LENGTH": self.ASR_CHUNK_LENGTH,
            "ASR_API_URL": self.ASR_API_URL or "",
            "ASR_REMOTE_MODEL": self.ASR_REMOTE_MODEL or "",
            # FFmpeg
            "FFMPEG_PATH": self.FFMPEG_PATH or "",
            # Device
            "DEVICE_PREFERENCE": self.DEVICE_PREFERENCE,
            "COMPUTE_TYPE": self.COMPUTE_TYPE,
            # Chunking
            "CHUNKING_THRESHOLD_MINUTES": self.CHUNKING_THRESHOLD_MINUTES,
            "CHUNK_OVERLAP_SECONDS": self.CHUNK_OVERLAP_SECONDS,
            # Audio
            "AUDIO_SAMPLE_RATE": self.AUDIO_SAMPLE_RATE,
            "AUDIO_CHANNELS": self.AUDIO_CHANNELS,
            "DEDUPLICATE_THRESHOLD_SECONDS": self.DEDUPLICATE_THRESHOLD_SECONDS,
            # ASS Style
            "ASS_PLAYRES_X": self.ASS_PLAYRES_X,
            "ASS_PLAYRES_Y": self.ASS_PLAYRES_Y,
            "ASS_FONT_NAME": self.ASS_FONT_NAME,
            "ASS_FONT_SIZE_ORIGINAL": self.ASS_FONT_SIZE_ORIGINAL,
            "ASS_FONT_SIZE_TRANSLATED": self.ASS_FONT_SIZE_TRANSLATED,
            # SSE
            "SSE_QUEUE_MAXSIZE": self.SSE_QUEUE_MAXSIZE,
            "SSE_KEEPALIVE_TIMEOUT": self.SSE_KEEPALIVE_TIMEOUT,
            # TTS / Dubbing
            "TTS_MODE": self.TTS_MODE,
            "QWEN_AUTO_CPU_FALLBACK": self.QWEN_AUTO_CPU_FALLBACK,
            "TTS_API_URL": self.TTS_API_URL,
            "TTS_API_KEY": "********" if self.TTS_API_KEY else "",
            "TTS_MODEL": self.TTS_MODEL,
            "TTS_DEFAULT_VOICE": self.TTS_DEFAULT_VOICE,
            "TTS_SPEED": self.TTS_SPEED,
            "DUB_SAMPLE_RATE": self.DUB_SAMPLE_RATE,
            "KOKORO_MODEL_PATH": self.KOKORO_MODEL_PATH,
            "KOKORO_VOICES_PATH": self.KOKORO_VOICES_PATH,
        }

    def _validate_url(self, key: str, value: str) -> str:
        """Validate URL fields to mitigate SSRF (only http/https, block known metadata endpoints)."""
        import urllib.parse
        if not value:
            return value
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Field '{key}' must use http or https scheme.")
        hostname = parsed.hostname or ""
        if hostname in ("169.254.169.254", "metadata.google.internal"):
            raise ValueError(f"Field '{key}' points to a restricted metadata endpoint.")
        return value

    def update(self, updates: Dict[str, Any]):
        """Validate a complete candidate update, then persist it transactionally."""
        normalized: Dict[str, Any] = {}
        for key, value in updates.items():
            definition = SETTINGS_BY_KEY.get(key)
            if definition is None:
                logger.warning(f"Ignoring non-editable field '{key}'")
                continue
            value = normalize_setting_value(definition, value)
            if definition.secret and value == MASKED_SECRET:
                continue
            if definition.format == "url" and value:
                value = self._validate_url(key, str(value))
            normalized[key] = value

        snapshot = {key: getattr(self, key) for key in normalized}
        try:
            for key, value in normalized.items():
                setattr(self, key, value)
            self.save_to_file()
        except Exception:
            for key, value in snapshot.items():
                setattr(self, key, value)
            raise


settings = Settings()
