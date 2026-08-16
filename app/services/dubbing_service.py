"""Dubbing service: synthesize speech from subtitles via Speaches TTS, align to
timeline, and mux into video replacing the original audio track.

Architecturally mirrors `VideoBurnService`: synchronous entry point `dub()`
scheduled via BackgroundTasks, emits SSE progress via `emit_event`, persists
state to history, and uses FFmpeg for post-processing.
"""
import asyncio
import json
import logging
import re
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.core.provider_lifecycle import NoopProviderLifecycle, ProviderLifecycle
from app.core.history import history_manager
from app.core.events import emit_event
from app.core.schemas import TranscriptionResult
from app.services.edge_tts_service import EdgeTTSService

logger = logging.getLogger(__name__)

# Balanced timing policy: keep one tempo for the whole track, but do not let a
# few unusually long lines make every speaker sound rushed.
MAX_ATEMPO = 1.2
MAX_SEGMENT_ATEMPO = 1.5
ALIGNMENT_TEMPO_PERCENTILE = 0.75
MAX_GAP_BORROW_SECONDS = 0.5
MIN_INTER_SEGMENT_GAP_SECONDS = 0.08
TRIM_FADE_SECONDS = 0.04
NATURAL_MAX_UNIFORM_TEMPO = 1.1
NATURAL_SENTENCE_GAP_SECONDS = 0.12
NATURAL_MAX_GROUP_SECONDS = 12.0
# Allowed clone sample extensions
_CLONE_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")


class DubbingCancelled(RuntimeError):
    pass


def _is_kokoro_mode() -> bool:
    return settings.TTS_MODE in {"local", "kokoro"}

# Voice prefix → (frontend language code, kokoro-onnx lang parameter).
# First letter encodes language; second letter encodes gender (f/m).
_VOICE_PREFIX_MAP = {
    "a": ("en-us", "en-us"),   # American English
    "b": ("en-gb", "en-gb"),   # British English
    "z": ("zh", "cmn"),        # Mandarin Chinese
    "j": ("ja", "ja"),         # Japanese
    "e": ("es", "es"),         # Spanish
    "f": ("fr-fr", "fr-fr"),   # French
    "h": ("hi", "hi"),         # Hindi
    "i": ("it", "it"),         # Italian
    "p": ("pt-br", "pt-br"),   # Portuguese (Brazil)
}


def _voice_metadata(voice_id: str) -> Dict[str, str]:
    """Derive language and gender from a Kokoro voice name prefix.

    Voice names follow ``{lang}{gender}_{name}`` — e.g. ``zf_xiaoxiao``
    → language ``zh``, gender ``female``.
    """
    if not voice_id or len(voice_id) < 2:
        return {"id": voice_id, "language": "", "gender": ""}
    prefix = voice_id[0].lower()
    gender_char = voice_id[1].lower()
    lang, _ = _VOICE_PREFIX_MAP.get(prefix, ("", ""))
    gender = "female" if gender_char == "f" else ("male" if gender_char == "m" else "")
    return {"id": voice_id, "language": lang, "gender": gender}


def _voice_to_kokoro_lang(voice_id: str) -> str:
    """Return the kokoro-onnx ``lang`` parameter for phonemization."""
    prefix = (voice_id or "")[:1].lower()
    _, kokoro_lang = _VOICE_PREFIX_MAP.get(prefix, ("", "en-us"))
    return kokoro_lang or "en-us"


# Kokoro v1.0 ships 54 voices across 8 languages (en, zh, ja, es, fr, hi, it, pt-br).
# Used as the static voice catalogue in local mode so the UI dropdown is populated
# without loading the ~300 MB ONNX model.  Korean is NOT supported (no kf_*/km_*).
_KOKORO_V1_VOICES = [
    # American English (en-us)
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa",
    # British English (en-gb)
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    # Mandarin Chinese (cmn)
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
    # Japanese (ja)
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    # Spanish (es)
    "ef_dora", "em_alex", "em_santa",
    # French (fr-fr)
    "ff_siwis",
    # Hindi (hi)
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    # Italian (it)
    "if_sara", "im_nicola",
    # Portuguese (pt-br)
    "pf_dora", "pm_alex", "pm_santa",
]


class DubbingService:
    # Local Kokoro singleton (lazy-loaded, thread-safe)
    _kokoro_instance = None
    _kokoro_lock = threading.Lock()
    _local_voices_cache: Optional[List[Dict[str, str]]] = None
    # Misaki Chinese G2p converter (lazy-loaded, thread-safe).
    # kokoro-onnx uses espeak for phonemization, but the Kokoro model was
    # trained with misaki phonemes. For Chinese, espeak's "cmn" mode lacks
    # tone markers, producing dialect-like pronunciation. Misaki includes
    # proper tone markers (→↘↓↗ for the four Mandarin tones).
    _zh_g2p = None
    _zh_g2p_lock = threading.Lock()
    # Misaki Japanese G2P is required for Kokoro Japanese voices. The generic
    # kokoro-onnx tokenizer only documents English and can produce massively
    # inflated Japanese phoneme streams.
    _ja_g2p = None
    _ja_g2p_lock = threading.Lock()

    @staticmethod
    def _default_kokoro_dir() -> Path:
        """Keep large model assets in the repository-level models directory."""
        return settings.BASE_DIR.parent / "models" / "kokoro"

    def __init__(
        self,
        *,
        http_client_factory: Callable = httpx.AsyncClient,
        lifecycle: ProviderLifecycle | None = None,
        edge_provider=None,
    ):
        self.ffmpeg_path = settings.ffmpeg_path
        self._http_client_factory = http_client_factory
        self._lifecycle = lifecycle or NoopProviderLifecycle()
        self._edge_provider = edge_provider or EdgeTTSService(
            self.ffmpeg_path, sample_rate=settings.DUB_SAMPLE_RATE
        )
        self._cancel_events: Dict[str, threading.Event] = {}
        self._finished_events: Dict[str, threading.Event] = {}

    def request_cancel(self, task_id: str) -> bool:
        event = getattr(self, "_cancel_events", {}).get(task_id)
        if not event:
            return False
        event.set()
        return True

    def wait_for_stop(self, task_id: str, timeout: float = 15) -> bool:
        event = getattr(self, "_finished_events", {}).get(task_id)
        return True if event is None else event.wait(timeout)

    def _check_cancelled(self, task_id: str) -> None:
        event = getattr(self, "_cancel_events", {}).get(task_id)
        if event and event.is_set():
            raise DubbingCancelled("Dubbing was cancelled")

    @staticmethod
    def provider_for_language(language: str) -> str:
        normalized = str(language or "").strip().lower()
        if normalized in {"ko", "ko-kr", "korean", "한국어", "韩语", "韓語"}:
            return "edge"
        if _is_kokoro_mode():
            return "kokoro"
        return settings.TTS_MODE

    # ------------------------------------------------------------------
    # Shared helpers (mirror VideoBurnService)
    # ------------------------------------------------------------------
    def _verify_ffmpeg(self) -> None:
        if not self.ffmpeg_path:
            raise RuntimeError(
                "FFmpeg executable not found. Please install FFmpeg or place ffmpeg.exe in ffmpeg/ or bin/ (Windows)."
            )

    def _load_transcription(self, path: str) -> Optional[TranscriptionResult]:
        try:
            p = Path(path)
            if not p.exists():
                return None
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TranscriptionResult(**data)
        except Exception as e:
            logger.error(f"Failed to load transcription from {path}: {e}")
            return None

    def _to_download_url(self, file_path: Path) -> str:
        try:
            rel = file_path.relative_to(settings.OUTPUT_DIR)
            return f"/static/output/{rel.as_posix()}"
        except ValueError:
            return str(file_path)

    def _parse_time(self, time_str: str) -> float:
        parts = time_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])

    # ------------------------------------------------------------------
    # Subtitle resolution
    # ------------------------------------------------------------------
    def _resolve_source_json(
        self, task: Dict[str, Any], target_lang: Optional[str]
    ) -> Tuple[Optional[TranscriptionResult], str]:
        """Pick the subtitle JSON to dub. Priority:
        1. translations[target_lang] if target_lang given and present
        2. translation_path (main translation)
        3. transcription_path (original)
        Returns (result, lang_label).
        """
        translations = task.get("translations") or {}

        if target_lang and target_lang != "source":
            translation_path = translations.get(target_lang)
            if not translation_path and task.get("target_lang") == target_lang:
                translation_path = task.get("translation_path")
            if translation_path:
                result = self._load_transcription(translation_path)
                if result:
                    return result, target_lang
            raise ValueError(
                f"未找到 {target_lang} 译文，已停止配音以避免误用其他语言译文。"
            )

        if target_lang == "source" or (not target_lang and not task.get("translation_path")):
            result = self._load_transcription(task["transcription_path"])
            return result, "source"

        trans_path = task.get("translation_path")
        if trans_path:
            result = self._load_transcription(trans_path)
            if result:
                return result, target_lang or result.language or "target"

        if task.get("transcription_path"):
            result = self._load_transcription(task["transcription_path"])
            return result, "source"

        return None, ""

    # ------------------------------------------------------------------
    # Speaches TTS communication
    # ------------------------------------------------------------------
    @property
    def _base_url(self) -> str:
        return settings.TTS_API_URL.rstrip("/")

    @property
    def _speech_url(self) -> str:
        return f"{self._base_url}/v1/audio/speech"

    @property
    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if settings.TTS_API_KEY:
            h["Authorization"] = f"Bearer {settings.TTS_API_KEY}"
        return h

    async def _synthesize_one(
        self, client: Optional[httpx.AsyncClient], text: str, voice: str, speed: float,
        provider: str | None = None,
    ) -> bytes:
        """Synthesize a single segment. Returns WAV bytes."""
        provider = provider or ("kokoro" if _is_kokoro_mode() else settings.TTS_MODE)
        if provider == "edge":
            return await self._edge_provider.synthesize(text, voice, speed)
        if provider == "kokoro":
            return await asyncio.to_thread(
                self._synthesize_one_local, text, voice, speed
            )
        # Speaches mode: all synthesis goes through the HTTP API.
        # (Note: Speaches has a bug where Chinese voices crash because it passes
        # lang="zh" to espeak which requires "cmn". We do NOT auto-route here —
        # users should switch to local mode for Chinese dubbing.)
        payload = {
            "model": settings.TTS_MODEL,
            "input": text,
            "voice": voice,
            "response_format": "wav",
            "speed": speed,
        }
        last_err: Optional[Exception] = None
        max_retries = max(settings.LLM_MAX_RETRIES, 1)
        for attempt in range(max_retries + 1):
            try:
                resp = await client.post(
                    self._speech_url, headers=self._headers, json=payload, timeout=120.0
                )
                if resp.status_code == 404 or resp.status_code == 400:
                    # Voice not registered / bad request — no point retrying
                    raise RuntimeError(
                        f"TTS rejected request ({resp.status_code}): {resp.text[:300]}. "
                        f"Check that voice '{voice}' is registered in Speaches."
                    )
                resp.raise_for_status()
                return resp.content
            except RuntimeError:
                raise
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    wait = settings.LLM_RETRY_BACKOFF_BASE * (attempt + 1)
                    logger.warning(f"TTS attempt {attempt + 1} failed: {e}; retrying in {wait}s")
                    await asyncio.sleep(wait)
        raise RuntimeError(f"TTS synthesis failed after retries: {last_err}")

    async def _fetch_voice_fallbacks(self, primary_voice: str) -> List[str]:
        """Fetch voices sharing the same language as ``primary_voice`` (excluding it).

        Used to gracefully fall back when a specific voice crashes the TTS
        server mid-synthesis (e.g. some Kokoro voices trigger Speaches bugs).
        Returns an empty list on any failure (fallback simply disabled).
        """
        if _is_kokoro_mode():
            meta = _voice_metadata(primary_voice)
            lang = meta.get("language", "")
            if not lang:
                return []
            return [
                v["id"]
                for v in self._get_local_voices()
                if v.get("language") == lang and v["id"] != primary_voice
            ]
        try:
            async with self._http_client_factory(
                timeout=10.0, verify=settings.VERIFY_SSL
            ) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/audio/voices", headers=self._headers
                )
                resp.raise_for_status()
                data = resp.json()
            voices = (
                data.get("voices", []) if isinstance(data, dict) else data
            )
            if not isinstance(voices, list):
                return []
            # Find the primary voice's language
            primary_lang = ""
            for v in voices:
                if not isinstance(v, dict):
                    continue
                if v.get("id") == primary_voice or v.get("name") == primary_voice:
                    primary_lang = v.get("language", "") or ""
                    break
            if not primary_lang:
                return []
            # Collect same-language voices excluding the primary
            return [
                (v.get("id") or v.get("name") or "")
                for v in voices
                if isinstance(v, dict)
                and (v.get("id") or v.get("name") or "")
                and (v.get("id") or v.get("name")) != primary_voice
                and v.get("language") == primary_lang
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch voice fallbacks: {e}")
            return []

    # ------------------------------------------------------------------
    # Local Kokoro ONNX (in-process, no HTTP service required)
    # ------------------------------------------------------------------
    # Default download URLs for Kokoro v1.0 model files
    _KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
    _KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

    @staticmethod
    def _download_file(url: str, dest: str) -> None:
        """Download a file with progress logging."""
        import requests
        resp = requests.get(url, stream=True, timeout=300)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        last_logged = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0 and downloaded - last_logged >= 10 * 1024 * 1024:
                    logger.info(
                        f"  下载 {Path(dest).name}: "
                        f"{downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB"
                    )
                    last_logged = downloaded
        logger.info(f"  下载完成: {Path(dest).name} ({downloaded // 1024 // 1024}MB)")

    @classmethod
    def _get_kokoro(cls):
        """Lazy-load the Kokoro ONNX singleton (thread-safe).

        Uses ``KOKORO_MODEL_PATH`` / ``KOKORO_VOICES_PATH`` if configured.
        Otherwise downloads model files to ``models/kokoro/`` on first use.
        """
        if cls._kokoro_instance is None:
            with cls._kokoro_lock:
                if cls._kokoro_instance is None:
                    try:
                        from kokoro_onnx import Kokoro
                    except ImportError as e:
                        raise RuntimeError(
                            "本地 Kokoro 模式需要 kokoro-onnx 包。"
                            "请运行: pip install kokoro-onnx soundfile"
                        ) from e

                    model_path = settings.KOKORO_MODEL_PATH or ""
                    voices_path = settings.KOKORO_VOICES_PATH or ""

                    # Auto-download to models/kokoro/ if paths not configured
                    if not model_path or not voices_path:
                        kokoro_dir = cls._default_kokoro_dir()
                        kokoro_dir.mkdir(parents=True, exist_ok=True)
                        if not model_path:
                            model_path = str(kokoro_dir / "kokoro-v1.0.onnx")
                        if not voices_path:
                            voices_path = str(kokoro_dir / "voices-v1.0.bin")

                    # Download missing files
                    if not Path(model_path).exists():
                        logger.info(f"正在下载 Kokoro 模型文件...")
                        cls._download_file(cls._KOKORO_MODEL_URL, model_path)
                    if not Path(voices_path).exists():
                        logger.info(f"正在下载 Kokoro 音色文件...")
                        cls._download_file(cls._KOKORO_VOICES_URL, voices_path)

                    logger.info(
                        f"Loading Kokoro ONNX model "
                        f"(model={model_path}, voices={voices_path})..."
                    )
                    cls._kokoro_instance = Kokoro(model_path, voices_path)
                    logger.info("Kokoro ONNX model loaded successfully.")
        return cls._kokoro_instance

    @classmethod
    def _get_zh_g2p(cls):
        """Lazy-load the misaki Chinese G2p converter (thread-safe).

        kokoro-onnx uses espeak for phonemization, but the Kokoro model was
        trained with misaki phonemes.  For Chinese, espeak's "cmn" mode
        produces phonemes without tone markers, causing dialect-like
        pronunciation.  Misaki's ZHG2P produces proper tone markers
        (→↘↓↗ for the four Mandarin tones) that match the model's training
        data, resulting in correct standard Mandarin pronunciation.
        """
        if cls._zh_g2p is None:
            with cls._zh_g2p_lock:
                if cls._zh_g2p is None:
                    try:
                        from misaki.zh import ZHG2P
                    except ImportError as e:
                        raise RuntimeError(
                            "中文音素化需要 misaki 包。请运行: "
                            "pip install 'misaki[zh]'"
                        ) from e
                    cls._zh_g2p = ZHG2P()
                    logger.info("Misaki Chinese G2p converter initialized.")
        return cls._zh_g2p

    @classmethod
    def _get_ja_g2p(cls):
        """Lazy-load Misaki's Japanese G2P converter (thread-safe)."""
        if cls._ja_g2p is None:
            with cls._ja_g2p_lock:
                if cls._ja_g2p is None:
                    try:
                        from misaki.ja import JAG2P
                    except (ImportError, ModuleNotFoundError) as exc:
                        raise RuntimeError(
                            "日语 Kokoro 配音需要 Misaki 日语依赖。请运行: "
                            "pip install 'misaki[ja]>=0.9.0'"
                        ) from exc
                    cls._ja_g2p = JAG2P(version="pyopenjtalk")
                    logger.info("Misaki Japanese G2P converter initialized.")
        return cls._ja_g2p

    def _synthesize_one_local(
        self, text: str, voice: str, speed: float
    ) -> bytes:
        """Synthesize a single segment via in-process Kokoro ONNX. Returns WAV bytes."""
        kokoro = self._get_kokoro()
        try:
            import soundfile as sf
        except ImportError as e:
            raise RuntimeError(
                "本地 Kokoro 模式需要 soundfile 包。请运行: pip install soundfile"
            ) from e
        import io

        def create_once(chunk: str):
            # For Chinese voices (prefix "z"), use misaki phonemization which
            # includes tone markers matching the model's training data.
            if voice[:1].lower() == "z":
                g2p = self._get_zh_g2p()
                result = g2p(chunk)
                phonemes = result[0] if isinstance(result, tuple) else result
                return kokoro.create(
                    phonemes, voice=voice, speed=speed, is_phonemes=True
                )

            if voice[:1].lower() == "j":
                g2p = self._get_ja_g2p()
                result = g2p(chunk)
                phonemes = result[0] if isinstance(result, tuple) else result
                return kokoro.create(
                    phonemes, voice=voice, speed=speed, is_phonemes=True
                )

            lang = _voice_to_kokoro_lang(voice)
            try:
                return kokoro.create(chunk, voice=voice, lang=lang, speed=speed)
            except TypeError:
                # Older kokoro-onnx versions may not accept the speed kwarg.
                return kokoro.create(chunk, voice=voice, lang=lang)

        def create_samples(chunk: str):
            # Split before reaching Kokoro's roughly 510-phoneme ceiling. Some
            # runtime/model combinations truncate instead of raising IndexError,
            # so exception-only recovery can silently lose the end of a sentence.
            if len(chunk) > 180:
                left, right = self._split_local_tts_text(chunk)
                if left and right:
                    logger.info(
                        "Splitting long Kokoro utterance proactively: %s + %s characters",
                        len(left), len(right),
                    )
                    left_samples, left_rate = create_samples(left)
                    right_samples, right_rate = create_samples(right)
                    if left_rate != right_rate:
                        raise RuntimeError("Kokoro split synthesis returned inconsistent sample rates")
                    import numpy as np
                    pause = np.zeros(max(int(left_rate * 0.04), 1), dtype=left_samples.dtype)
                    return np.concatenate((left_samples, pause, right_samples)), left_rate
            try:
                return create_once(chunk)
            except IndexError as exc:
                # kokoro-onnx 0.4.x truncates inputs to 510 phonemes and then
                # indexes voice[510]. Split at a natural boundary and retry so
                # no spoken content is silently truncated.
                if "out of bounds" not in str(exc) or len(chunk) < 2:
                    raise
                left, right = self._split_local_tts_text(chunk)
                if not left or not right:
                    raise
                logger.warning(
                    "Kokoro input exceeded its phoneme limit; retrying as %s + %s characters",
                    len(left), len(right),
                )
                left_samples, left_rate = create_samples(left)
                right_samples, right_rate = create_samples(right)
                if left_rate != right_rate:
                    raise RuntimeError("Kokoro split synthesis returned inconsistent sample rates")
                import numpy as np
                pause = np.zeros(max(int(left_rate * 0.04), 1), dtype=left_samples.dtype)
                return np.concatenate((left_samples, pause, right_samples)), left_rate

        samples, sample_rate = create_samples(text)

        buf = io.BytesIO()
        sf.write(buf, samples, sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    @staticmethod
    def _split_local_tts_text(text: str) -> Tuple[str, str]:
        """Split near the middle, preferring sentence and phrase boundaries."""
        midpoint = len(text) // 2
        candidates = [
            index + 1
            for index, char in enumerate(text)
            if char in "。！？!?；;、，,：: " and 0 < index + 1 < len(text)
        ]
        split_at = min(candidates, key=lambda value: abs(value - midpoint)) if candidates else midpoint
        return text[:split_at].strip(), text[split_at:].strip()

    def _get_local_voices(self) -> List[Dict[str, str]]:
        """Return locally available Kokoro voices with language/gender metadata.

        If the Kokoro model is already loaded, queries its actual voice list;
        otherwise falls back to the static ``_KOKORO_V1_VOICES`` catalogue so
        the UI works without loading the ~300 MB model.
        """
        if self._local_voices_cache is None:
            voices = _KOKORO_V1_VOICES
            # If the model is already loaded, prefer its actual voice list
            if self._kokoro_instance is not None:
                try:
                    # get_voices() returns list[str], not a dict
                    available = self._kokoro_instance.get_voices()
                    if available:
                        voices = sorted(available)
                except Exception:
                    pass
            self._local_voices_cache = [_voice_metadata(v) for v in voices]
        return self._local_voices_cache

    async def _synthesize_all(
        self,
        segments: List,
        voice: str,
        speed: float,
        task_id: str,
        temp_dir: Path,
        provider: str | None = None,
    ) -> List[Path]:
        """Synthesize all segments sequentially, return list of raw WAV paths.

        If a voice fails after retries (e.g. server crash on a specific voice),
        automatically falls back to other voices of the same language. Once a
        fallback voice succeeds it is used for all remaining segments.
        """
        raw_paths: List[Path] = []
        total = len(segments)

        # Pre-fetch same-language fallback voices for resilience
        provider = provider or ("kokoro" if _is_kokoro_mode() else settings.TTS_MODE)
        fallback_voices = [] if provider == "edge" else await self._fetch_voice_fallbacks(voice)
        if fallback_voices:
            logger.info(
                f"Loaded {len(fallback_voices)} fallback voices for '{voice}': {fallback_voices}"
            )

        effective_voice = voice
        # In local mode no HTTP client is needed — kokoro-onnx runs in-process.
        # In speaches mode reuse a single client across all segments (connection pool).
        client = None if provider in {"kokoro", "edge"} else self._http_client_factory(
            verify=settings.VERIFY_SSL
        )
        try:
            await self._lifecycle.startup(
                task_id=task_id, stage="tts", provider=provider
            )
            for i, seg in enumerate(segments):
                self._check_cancelled(task_id)
                text = (seg.text or "").strip()
                if not text:
                    # Empty segment — write a tiny silent placeholder to keep indexing aligned
                    placeholder = temp_dir / f"raw_{i:06d}.wav"
                    self._write_silence(placeholder, 0.05)
                    raw_paths.append(placeholder)
                    continue

                # Try current voice first, then fallbacks
                voices_to_try = [effective_voice] + [
                    v for v in fallback_voices if v != effective_voice
                ]
                wav_bytes: Optional[bytes] = None
                last_err: Optional[Exception] = None
                for try_voice in voices_to_try:
                    try:
                        wav_bytes = await self._synthesize_one(
                            client, text, try_voice, speed, provider
                        )
                        if try_voice != effective_voice:
                            logger.warning(
                                f"Segment {i + 1}/{total}: switched to fallback voice "
                                f"'{try_voice}' (primary '{voice}' failed)"
                            )
                            effective_voice = try_voice  # switch permanently
                        break
                    except RuntimeError as e:
                        last_err = e
                        logger.warning(
                            f"Segment {i + 1}/{total}: voice '{try_voice}' failed: {e}"
                        )
                        continue

                if wav_bytes is None:
                    raise last_err or RuntimeError(
                        f"All voices failed for segment {i + 1}/{total}"
                    )

                raw_path = temp_dir / f"raw_{i:06d}.wav"
                with open(raw_path, "wb") as f:
                    f.write(wav_bytes)
                raw_paths.append(raw_path)
                progress = 5 + int((i + 1) / total * 65)
                emit_event(
                    task_id,
                    {
                        "status": "dubbing",
                        "message": f"正在合成语音 {i + 1}/{total}...",
                        "progress": progress,
                    },
                )
        finally:
            if client is not None:
                await client.aclose()
            await self._lifecycle.shutdown(
                task_id=task_id, stage="tts", provider=provider
            )
        return raw_paths

    # ------------------------------------------------------------------
    # FFmpeg audio helpers
    # ------------------------------------------------------------------
    def _run_ffmpeg(self, cmd: List[str], label: str) -> None:
        logger.info(f"{label}: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            err = (result.stderr or "")[-800:]
            raise RuntimeError(f"{label} failed: {err}")

    def _write_silence(self, out_path: Path, seconds: float) -> None:
        """Write a silent WAV of given duration (matched sample rate/channels)."""
        if seconds <= 0:
            seconds = 0.001
        cmd = [
            self.ffmpeg_path, "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=mono:sample_rate={settings.DUB_SAMPLE_RATE}",
            "-t", f"{seconds:.4f}",
            "-ar", str(settings.DUB_SAMPLE_RATE),
            "-ac", "1",
            str(out_path),
        ]
        self._run_ffmpeg(cmd, "write_silence")

    def _probe_duration(self, audio_path: Path) -> float:
        """Probe audio duration in seconds via ffmpeg stderr."""
        cmd = [self.ffmpeg_path, "-i", str(audio_path), "-f", "null", "-"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        # ffmpeg prints Duration: HH:MM:SS.xx to stderr
        m = re.search(r"Duration:\s+(\d+:\d+:\d+\.\d+)", result.stderr or "")
        if not m:
            return 0.0
        return self._parse_time(m.group(1))

    def _fit_segment_audio(
        self,
        src_wav: Path,
        slot_seconds: float,
        out_wav: Path,
        tempo_ratio: float = 1.0,
    ) -> Path:
        """Align a synthesized segment to its timeline slot.
        Use one shared tempo ratio for the whole timeline, then trim or pad the
        result to exactly ``slot_seconds``.
        """
        if slot_seconds <= 0:
            slot_seconds = 0.05
        d = self._probe_duration(src_wav)
        filters: List[str] = []
        required_ratio = d / slot_seconds if slot_seconds > 0 else 1.0
        # Keep the common track tempo natural, then speed up only the lines
        # that would otherwise be cut. This avoids making every line 1.5x
        # while preserving substantially more speech than a hard 1.2x cap.
        ratio = min(
            max(float(tempo_ratio), required_ratio, 1.0),
            MAX_SEGMENT_ATEMPO,
        )
        if ratio > 1.0:
            filters.append(f"atempo={ratio:.4f}")
        adjusted_duration = d / ratio if ratio > 0 else d
        if adjusted_duration > slot_seconds:
            fade_duration = min(TRIM_FADE_SECONDS, slot_seconds)
            fade_start = max(slot_seconds - fade_duration, 0.0)
            filters.append(
                f"afade=t=out:st={fade_start:.4f}:d={fade_duration:.4f}"
            )
            filters.append(f"atrim=0:{slot_seconds:.4f}")
            filters.append("asetpts=PTS-STARTPTS")
        # Pad trailing silence to reach exact slot length
        pad_duration = max(slot_seconds - min(adjusted_duration, slot_seconds), 0)
        filters.append(f"apad=pad_dur={pad_duration:.4f}")
        filters.append(f"atrim=0:{slot_seconds:.4f}")
        filters.append("asetpts=PTS-STARTPTS")
        logger.info(
            "Dubbing timing: source=%s raw=%.3fs slot=%.3fs base=%.3fx "
            "effective=%.3fx required=%.3fx adjusted=%.3fs pad=%.3fs trimmed=%s",
            src_wav.name, d, slot_seconds, tempo_ratio, ratio, required_ratio, adjusted_duration,
            pad_duration, adjusted_duration > slot_seconds,
        )
        cmd = [
            self.ffmpeg_path, "-y",
            "-i", str(src_wav),
            "-af", ",".join(filters),
            "-ar", str(settings.DUB_SAMPLE_RATE),
            "-ac", "1",
            str(out_wav),
        ]
        self._run_ffmpeg(cmd, f"fit_segment({slot_seconds:.2f}s)")
        return out_wav

    @staticmethod
    def _is_sentence_end(text: str) -> bool:
        stripped = (text or "").strip()
        if not stripped or stripped.endswith(("...", "…")):
            return False
        return bool(re.search(r"[。！？!?][\"'”’）)]?$", stripped)) or bool(
            re.search(r"\.[\"'”’）)]?$", stripped)
        )

    @staticmethod
    def _join_dubbing_fragments(parts: List[str]) -> str:
        """Join ASR fragments without inserting artificial pauses in CJK text."""
        if not parts:
            return ""
        result = parts[0]
        cjk = r"\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af"
        closing_punctuation = "，。！？；：、,.!?;:）】》”’"
        for part in parts[1:]:
            if not part:
                continue
            previous = result[-1:]
            following = part[:1]
            if (
                following in closing_punctuation
                or (re.match(f"[{cjk}]", previous) and re.match(f"[{cjk}]", following))
            ):
                result += part
            else:
                result += " " + part
        return result

    def _group_segments_for_dubbing(self, segments: List) -> List:
        """Combine subtitle fragments into sentence-sized TTS utterances."""
        groups: List = []
        current: List = []

        def flush() -> None:
            if not current:
                return
            text = self._join_dubbing_fragments([
                (segment.text or "").strip()
                for segment in current
                if (segment.text or "").strip()
            ])
            if text:
                groups.append(
                    SimpleNamespace(
                        start=float(current[0].start),
                        end=float(current[-1].end),
                        text=text,
                    )
                )
            current.clear()

        for segment in segments:
            if not (segment.text or "").strip():
                continue
            if current:
                prospective_duration = float(segment.end) - float(current[0].start)
                gap = float(segment.start) - float(current[-1].end)
                if gap > 0.75 or prospective_duration > NATURAL_MAX_GROUP_SECONDS:
                    flush()
            current.append(segment)
            if self._is_sentence_end(segment.text):
                flush()
        flush()
        return groups

    def _normalize_natural_audio(
        self, src_wav: Path, out_wav: Path, tempo_ratio: float
    ) -> Path:
        filters: List[str] = []
        if tempo_ratio > 1.0005:
            filters.append(f"atempo={tempo_ratio:.4f}")
        cmd = [self.ffmpeg_path, "-y", "-i", str(src_wav)]
        if filters:
            cmd.extend(["-af", ",".join(filters)])
        cmd.extend([
            "-ar", str(settings.DUB_SAMPLE_RATE),
            "-ac", "1",
            str(out_wav),
        ])
        self._run_ffmpeg(cmd, f"normalize_natural({tempo_ratio:.3f}x)")
        return out_wav

    @staticmethod
    def _natural_timeline_end(
        segments: List, durations: List[float], tempo_ratio: float
    ) -> float:
        cursor = 0.0
        has_speech = False
        for segment, duration in zip(segments, durations):
            earliest = cursor + (NATURAL_SENTENCE_GAP_SECONDS if has_speech else 0.0)
            actual_start = max(float(segment.start), earliest)
            cursor = actual_start + (duration / tempo_ratio)
            has_speech = True
        return cursor

    @staticmethod
    def _percentile(values: List[float], percentile: float) -> float:
        """Return a linearly interpolated percentile without a NumPy dependency."""
        if not values:
            return 1.0
        ordered = sorted(float(value) for value in values)
        if len(ordered) == 1:
            return ordered[0]
        position = min(max(percentile, 0.0), 1.0) * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

    @staticmethod
    def _timeline_slot_end(
        segments: List, index: int, total_duration: float
    ) -> float:
        """Allow speech to use a small following gap without crossing the next line."""
        segment_end = float(segments[index].end)
        boundary = (
            float(segments[index + 1].start)
            if index + 1 < len(segments)
            else total_duration
        )
        if index + 1 < len(segments):
            # Reserve a small audible separation before the next utterance.
            boundary = max(float(segments[index].start), boundary - MIN_INTER_SEGMENT_GAP_SECONDS)
        if boundary < segment_end:
            return boundary
        return min(boundary, segment_end + MAX_GAP_BORROW_SECONDS)

    def _build_timeline(
        self,
        segments: List,
        raw_paths: List[Path],
        total_duration: float,
        temp_dir: Path,
    ) -> Path:
        """Build a natural, non-overlapping timeline without per-line trimming."""
        fitted_dir = temp_dir / "fitted"
        fitted_dir.mkdir(parents=True, exist_ok=True)
        list_file = temp_dir / "concat_list.txt"
        entries: List[str] = []

        durations = [self._probe_duration(raw) for raw in raw_paths]
        natural_end = self._natural_timeline_end(segments, durations, 1.0)
        timeline_tempo = 1.0
        if natural_end > total_duration + 0.01:
            fastest_end = self._natural_timeline_end(
                segments, durations, NATURAL_MAX_UNIFORM_TEMPO
            )
            if fastest_end > total_duration + 0.01:
                overflow = fastest_end - total_duration
                raise ValueError(
                    "自然配音内容过长，即使统一加速到 "
                    f"{NATURAL_MAX_UNIFORM_TEMPO:.2f}x 仍超出视频 {overflow:.2f} 秒。"
                    "请精简译文或提高配音语速后重试。"
                )
            low, high = 1.0, NATURAL_MAX_UNIFORM_TEMPO
            for _ in range(16):
                middle = (low + high) / 2
                if self._natural_timeline_end(segments, durations, middle) > total_duration:
                    low = middle
                else:
                    high = middle
            timeline_tempo = high
        logger.info(
            "Dubbing natural timeline uses one uniform tempo %.3fx "
            "(groups=%s, natural_end=%.3fs, video=%.3fs)",
            timeline_tempo,
            len(segments),
            natural_end,
            total_duration,
        )

        cursor = 0.0
        has_speech = False
        for i, (seg, raw, raw_duration) in enumerate(zip(segments, raw_paths, durations)):
            earliest = cursor + (NATURAL_SENTENCE_GAP_SECONDS if has_speech else 0.0)
            actual_start = max(float(seg.start), earliest)
            gap = actual_start - cursor
            if gap > 0.005:
                gap_wav = fitted_dir / f"gap_{i:06d}.wav"
                self._write_silence(gap_wav, gap)
                entries.append(f"file '{gap_wav.as_posix()}'")

            fitted_wav = fitted_dir / f"seg_{i:06d}.wav"
            self._normalize_natural_audio(raw, fitted_wav, timeline_tempo)
            entries.append(f"file '{fitted_wav.as_posix()}'")
            cursor = actual_start + raw_duration / timeline_tempo
            has_speech = True

        # Keep the dub track as long as the source video. Without this silence,
        # muxing with -shortest truncates videos that have no subtitle near the end.
        trailing_gap = total_duration - cursor
        if trailing_gap > 0.005:
            tail_wav = fitted_dir / "gap_trailing.wav"
            self._write_silence(tail_wav, trailing_gap)
            entries.append(f"file '{tail_wav.as_posix()}'")

        if not entries:
            raise ValueError("No usable subtitle segments available for dubbing")

        with open(list_file, "w", encoding="utf-8") as f:
            f.write("\n".join(entries) + "\n")

        out_path = temp_dir / "dub_track.wav"
        cmd = [
            self.ffmpeg_path, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-ar", str(settings.DUB_SAMPLE_RATE),
            "-ac", "1",
            str(out_path),
        ]
        self._run_ffmpeg(cmd, "build_timeline")
        return out_path

    def _mux_video(
        self,
        video_path: Path,
        dub_audio: Path,
        out_path: Path,
        total_duration: float,
        task_id: str,
    ) -> None:
        """Replace the original audio track with the dub track."""
        cmd = [
            self.ffmpeg_path, "-y",
            "-i", str(video_path),
            "-i", str(dub_audio),
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
        logger.info(f"Starting mux for task {task_id}: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        last_progress = 90

        def read_stderr():
            nonlocal last_progress
            for line in iter(process.stderr.readline, b""):
                line_str = line.decode("utf-8", errors="replace")
                m = re.search(r"time=(\d+:\d+:\d+\.\d+)", line_str)
                if m and total_duration > 0:
                    cur = self._parse_time(m.group(1))
                    progress = min(90 + int((cur / total_duration) * 10), 99)
                    if progress > last_progress:
                        last_progress = progress
                        emit_event(
                            task_id,
                            {
                                "status": "dubbing",
                                "message": "正在合并音视频...",
                                "progress": progress,
                            },
                        )
            process.stderr.close()

        t = threading.Thread(target=read_stderr, daemon=True)
        t.start()
        while process.poll() is None:
            try:
                self._check_cancelled(task_id)
            except DubbingCancelled:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                raise
            time.sleep(0.1)
        t.join(timeout=2)
        if process.returncode != 0:
            err_bytes = process.stderr.read() if process.stderr else b""
            err = err_bytes.decode("utf-8", errors="replace")[-800:] if err_bytes else "Unknown FFmpeg error"
            raise RuntimeError(f"FFmpeg mux failed: {err}")

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------
    def _prepare_clone_voice(self, clone_sample_path: str, task_id: str) -> str:
        """Copy the uploaded clone sample into the Speaches voices directory and
        return the voice id (filename stem). Requires TTS_VOICES_DIR configured.
        """
        if not settings.TTS_VOICES_DIR:
            raise RuntimeError(
                "语音克隆需要配置 TTS_VOICES_DIR（Speaches voices 目录）。"
                "请在 config.yaml 设置后将样本放入该目录，或使用预设音色。"
            )
        voices_dir = Path(settings.TTS_VOICES_DIR)
        voices_dir.mkdir(parents=True, exist_ok=True)
        src = Path(clone_sample_path)
        if not src.exists():
            raise RuntimeError(f"克隆样本文件不存在: {clone_sample_path}")
        if src.suffix.lower() not in _CLONE_EXTS:
            raise RuntimeError(f"不支持的样本格式: {src.suffix}")
        ts = int(time.time())
        voice_id = f"clone_{task_id}_{ts}"
        dest = voices_dir / f"{voice_id}{src.suffix}"
        shutil.copyfile(str(src), str(dest))
        logger.info(f"Copied clone sample to {dest}, voice_id={voice_id}")
        return voice_id

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def dub(
        self,
        task_id: str,
        target_lang: Optional[str],
        voice: str,
        speed: float,
        clone_sample_path: Optional[str] = None,
    ) -> None:
        task = history_manager.get_task(task_id)
        if not task:
            raise ValueError("Task not found")

        video_path = task.get("upload_path") or task.get("source_path")
        if not video_path:
            raise ValueError("Source video path not found")
        video_path_obj = Path(video_path)
        if not video_path_obj.exists():
            raise ValueError("Source video file not found")

        if settings.TTS_MODE == "speaches" and not settings.TTS_API_URL:
            raise ValueError("TTS_API_URL not configured")

        self._verify_ffmpeg()

        # Voice cloning requires Speaches' voice directory; local Kokoro uses
        # fixed voice embeddings and cannot accept arbitrary clone samples.
        if clone_sample_path and _is_kokoro_mode():
            raise ValueError(
                "语音克隆仅在 Speaches (API) 模式下可用。"
                "请将 TTS_MODE 改为 speaches 或使用预设音色。"
            )

        # Resolve voice (handle clone mode)
        effective_voice = voice
        if clone_sample_path:
            effective_voice = self._prepare_clone_voice(clone_sample_path, task_id)

        # Resolve subtitle source
        result, lang_label = self._resolve_source_json(task, target_lang)
        if not result or not result.segments:
            raise ValueError("No subtitle data available for dubbing")
        provider = self.provider_for_language(getattr(result, "language", None) or lang_label)
        if provider == "edge" and clone_sample_path:
            raise ValueError("Edge 韩语在线配音不支持语音克隆，请使用标准韩语音色。")

        temp_dir = Path(settings.TEMP_DIR) / f"dub_{task_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        dub_audio_url: Optional[str] = None
        dub_video_url: Optional[str] = None
        cancel_event = threading.Event()
        finished_event = threading.Event()
        if not hasattr(self, "_cancel_events"):
            self._cancel_events = {}
        if not hasattr(self, "_finished_events"):
            self._finished_events = {}
        self._cancel_events[task_id] = cancel_event
        self._finished_events[task_id] = finished_event
        try:
            history_manager.update_task(
                task_id,
                {
                    "dubbing_status": "processing",
                    "dubbing_voice": effective_voice,
                    "dubbing_target_lang": lang_label,
                    "dubbing_speed": speed,
                    "dubbing_error": None,
                },
            )
            emit_event(
                task_id,
                {"status": "dubbing", "message": "正在准备配音...", "progress": 5},
            )

            segments = result.segments
            dubbing_segments = self._group_segments_for_dubbing(segments)
            logger.info(
                "Dubbing grouped %s subtitle fragments into %s natural utterances",
                len(segments),
                len(dubbing_segments),
            )
            subtitle_duration = max(float(segments[-1].end), 0.1)
            probed_video_duration = self._probe_duration(video_path_obj)
            total_duration = max(probed_video_duration, subtitle_duration)

            # Stage B: synthesize each segment
            logger.info(f"Dubbing task {task_id}: {len(segments)} segments, voice={effective_voice}")
            raw_paths = asyncio.run(
                self._synthesize_all(dubbing_segments, effective_voice, speed, task_id, temp_dir, provider)
            )

            # Stage C: align + build full track
            emit_event(
                task_id,
                {"status": "dubbing", "message": "正在对齐时间轴...", "progress": 70},
            )
            dub_track = self._build_timeline(
                dubbing_segments, raw_paths, total_duration, temp_dir
            )

            # Persist dub audio
            settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stem = Path(task["filename"]).stem
            ts = time.strftime("%Y%m%d_%H%M%S")
            audio_name = f"{stem}_dubbing_{lang_label}_{ts}.wav"
            audio_out = settings.OUTPUT_DIR / audio_name
            shutil.copyfile(str(dub_track), str(audio_out))
            dub_audio_url = self._to_download_url(audio_out)
            history_manager.update_task(
                task_id, {"dubbing_audio_path": dub_audio_url}
            )

            # Stage D: mux into video
            emit_event(
                task_id,
                {"status": "dubbing", "message": "正在合并音视频...", "progress": 90},
            )
            video_name = f"{stem}_dubbed_{lang_label}_{ts}.mp4"
            video_out = settings.OUTPUT_DIR / video_name
            self._mux_video(video_path_obj, dub_track, video_out, total_duration, task_id)
            dub_video_url = self._to_download_url(video_out)

            history_manager.update_task(
                task_id,
                {
                    "dubbing_status": "completed",
                    "dubbing_audio_path": dub_audio_url,
                    "dubbing_raw_video_path": dub_video_url,
                    "dubbing_video_path": dub_video_url,
                    "dubbing_burn_subtitles": False,
                },
            )
            emit_event(
                task_id,
                {"status": "dubbing_completed", "message": "配音完成", "progress": 100},
            )
            logger.info(f"Dubbing completed for task {task_id}: {video_out}")

        except DubbingCancelled as e:
            history_manager.update_task(
                task_id, {"dubbing_status": "cancelled", "dubbing_error": str(e)}
            )
            emit_event(task_id, {"status": "dubbing_cancelled", "message": str(e), "progress": 0})
            raise
        except Exception as e:
            logger.exception(f"Dubbing failed for task {task_id}")
            history_manager.update_task(
                task_id,
                {"dubbing_status": "failed", "dubbing_error": str(e)},
            )
            emit_event(
                task_id,
                {"status": "dubbing_failed", "message": str(e), "progress": 0},
            )
            raise
        finally:
            # Clean up temp dir
            try:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            finished_event.set()
            self._cancel_events.pop(task_id, None)
            self._finished_events.pop(task_id, None)


dubbing_service = DubbingService()
