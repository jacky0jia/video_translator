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
from app.core.gpu_lifecycle import gpu_provider_lifecycle
from app.core.provider_lifecycle import NoopProviderLifecycle, ProviderLifecycle
from app.core.history import history_manager
from app.core.events import emit_event
from app.services.hardware_service import hardware_service
from app.core.schemas import TranscriptionResult
from app.services.edge_tts_service import EdgeTTSService
from app.services.tts.edge import EdgeTTSProvider
from app.services.tts.base import TTSCancelled, TTSSynthesisRequest
from app.services.tts.kokoro import KokoroTTSProvider
from app.services.tts.cosyvoice import CosyVoiceTTSProvider
from app.services.tts.cosyvoice_bundle import (
    CosyVoiceBundle,
    default_cosyvoice_bundle_root,
)
from app.services.tts.qwen import QwenTTSProvider
from app.services.tts.qwen_bundle import (
    QwenTTSBundle,
    default_qwen_tts_bundle_root,
    qwen_worker_config_for_host,
)
from app.services.tts.registry import (
    DEFAULT_TTS_REGISTRY,
    TTSProviderRegistry,
    normalize_tts_mode,
    resolve_tts_route,
)
from app.services.tts.speaches import SpeachesTTSProvider

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
NATURAL_MAX_ADJUSTABLE_TEMPO = 1.12
NATURAL_SENTENCE_GAP_SECONDS = 0.32
NATURAL_MIN_SENTENCE_GAP_SECONDS = 0.20
NATURAL_MAX_UTTERANCE_LEAD_SECONDS = 0.6
NATURAL_MAX_FINAL_LEAD_SECONDS = 1.0
NATURAL_MAX_GROUP_SECONDS = 12.0
TTS_BOUNDARY_THRESHOLD_DB = -60
TTS_LEADING_SAFETY_SECONDS = 0.04
TTS_TRAILING_SAFETY_SECONDS = 0.08
# Allowed clone sample extensions
_CLONE_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")


class DubbingCancelled(RuntimeError):
    pass


def _is_kokoro_mode() -> bool:
    return normalize_tts_mode(settings.TTS_MODE) == "kokoro"

class DubbingService:
    def __init__(
        self,
        *,
        http_client_factory: Callable = httpx.AsyncClient,
        lifecycle: ProviderLifecycle | None = None,
        edge_provider=None,
        tts_registry: TTSProviderRegistry | None = None,
        gpu_lifecycle: ProviderLifecycle | None = None,
    ):
        self.ffmpeg_path = settings.ffmpeg_path
        self._http_client_factory = http_client_factory
        self._lifecycle = lifecycle or NoopProviderLifecycle()
        self._gpu_lifecycle = gpu_lifecycle or gpu_provider_lifecycle
        self._edge_provider = edge_provider or EdgeTTSService(
            self.ffmpeg_path, sample_rate=settings.DUB_SAMPLE_RATE
        )
        self.tts_registry = tts_registry or self._build_tts_registry()
        self._cancel_events: Dict[str, threading.Event] = {}
        self._finished_events: Dict[str, threading.Event] = {}

    def _build_tts_registry(self) -> TTSProviderRegistry:
        registry = TTSProviderRegistry(DEFAULT_TTS_REGISTRY.list())
        registry.register_provider(KokoroTTSProvider())
        registry.register_provider(EdgeTTSProvider(self._edge_provider))
        cosy_bundle = None

        def load_cosy_bundle():
            nonlocal cosy_bundle
            if cosy_bundle is None:
                cosy_bundle = CosyVoiceBundle.load(
                    default_cosyvoice_bundle_root(settings.BASE_DIR)
                )
            return cosy_bundle

        registry.register_provider(
            lambda: QwenTTSProvider(
                lambda: qwen_worker_config_for_host(
                    QwenTTSBundle.load(
                        default_qwen_tts_bundle_root(settings.BASE_DIR),
                        settings.BASE_DIR.parent / "models" / "voices" / "librivox_public_domain",
                    ).worker_config,
                    nvidia_available=hardware_service.nvidia_info is not None,
                    amd_available=hardware_service.amd_info is not None,
                    auto_cpu_fallback=settings.QWEN_AUTO_CPU_FALLBACK,
                )
            ),
            provider_id="qwen",
        )
        registry.register_provider(
            lambda: CosyVoiceTTSProvider(
                lambda: dict(load_cosy_bundle().worker_config),
                lambda: load_cosy_bundle().voices,
            ),
            provider_id="cosyvoice",
        )
        registry.register_provider(
            lambda: SpeachesTTSProvider(
                settings.TTS_API_URL,
                model=settings.TTS_MODEL,
                client_factory=self._http_client_factory,
                api_key=settings.TTS_API_KEY,
                verify_ssl=settings.VERIFY_SSL,
                retries=settings.LLM_MAX_RETRIES,
                retry_backoff=settings.LLM_RETRY_BACKOFF_BASE,
            ),
            provider_id="speaches",
        )
        return registry

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
        return resolve_tts_route(settings.TTS_MODE, language).provider_id

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
    # Provider-neutral single-segment synthesis
    # ------------------------------------------------------------------
    async def _synthesize_one(
        self, client: Optional[httpx.AsyncClient], text: str, voice: str, speed: float,
        provider: str | None = None, *, adapter=None, should_cancel=None, language: str = "",
    ) -> bytes:
        """Synthesize a single segment. Returns WAV bytes."""
        provider = provider or ("kokoro" if _is_kokoro_mode() else settings.TTS_MODE)
        adapter = adapter or self.tts_registry.create(provider)
        if provider == "speaches" and client is not None:
            adapter.bind_client(client)
        result = await adapter.synthesize(
            TTSSynthesisRequest(
                text=text,
                voice=voice,
                speed=speed,
                sample_rate=settings.DUB_SAMPLE_RATE,
                language=language,
            ),
            should_cancel=should_cancel,
        )
        return result.audio

    async def _fetch_voice_fallbacks(self, primary_voice: str, adapter) -> List[str]:
        """Fetch voices sharing the same language as ``primary_voice`` (excluding it).

        Used to gracefully fall back when a specific voice crashes the TTS
        server mid-synthesis (e.g. some Kokoro voices trigger Speaches bugs).
        Returns an empty list on any failure (fallback simply disabled).
        """
        try:
            voices = await adapter.list_voices()
            primary = next(
                (voice for voice in voices if voice.id == primary_voice or voice.name == primary_voice),
                None,
            )
            primary_lang = primary.language if primary else ""
            if not primary_lang:
                return []
            return [
                voice.id
                for voice in voices
                if voice.id and voice.id != primary_voice and voice.language == primary_lang
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch voice fallbacks: {e}")
            return []

    async def _synthesize_all(
        self,
        segments: List,
        voice: str,
        speed: float,
        task_id: str,
        temp_dir: Path,
        provider: str | None = None,
        language: str = "",
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
        # In local mode no HTTP client is needed — kokoro-onnx runs in-process.
        # In speaches mode reuse a single client across all segments (connection pool).
        client = (
            None
            if provider in {"kokoro", "edge", "cosyvoice", "qwen"}
            else self._http_client_factory(verify=settings.VERIFY_SSL)
        )
        adapter = self.tts_registry.create(provider)
        if provider == "speaches" and client is not None:
            adapter.bind_client(client)
        effective_voice = voice
        try:
            stage_lifecycle = self._gpu_lifecycle if provider == "qwen" else self._lifecycle
            await stage_lifecycle.startup(
                task_id=task_id, stage="tts", provider=provider
            )
            fallback_voices = (
                []
                if provider == "edge"
                else await self._fetch_voice_fallbacks(voice, adapter)
            )
            if fallback_voices:
                logger.info(
                    "Loaded %s fallback voices for '%s': %s",
                    len(fallback_voices),
                    voice,
                    fallback_voices,
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
                            client,
                            text,
                            try_voice,
                            speed,
                            provider,
                            adapter=adapter,
                            should_cancel=lambda: bool(
                                self._cancel_events.get(task_id)
                                and self._cancel_events[task_id].is_set()
                            ),
                            language=language,
                        )
                        if try_voice != effective_voice:
                            logger.warning(
                                f"Segment {i + 1}/{total}: switched to fallback voice "
                                f"'{try_voice}' (primary '{voice}' failed)"
                            )
                            effective_voice = try_voice  # switch permanently
                        break
                    except TTSCancelled as exc:
                        raise DubbingCancelled(str(exc)) from exc
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
            try:
                close_adapter = getattr(adapter, "close", None)
                if callable(close_adapter):
                    await close_adapter()
            finally:
                try:
                    if client is not None:
                        await client.aclose()
                finally:
                    await stage_lifecycle.shutdown(
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

    def _trim_outer_silence(self, src_wav: Path, out_wav: Path) -> Path:
        """Remove generated boundary silence without clipping quiet phonemes."""
        silence_filter = (
            "silenceremove="
            "start_periods=1:start_duration=0.03:"
            f"start_threshold={TTS_BOUNDARY_THRESHOLD_DB}dB:"
            f"start_silence={TTS_LEADING_SAFETY_SECONDS},"
            "areverse,"
            "silenceremove=start_periods=1:start_duration=0.03:"
            f"start_threshold={TTS_BOUNDARY_THRESHOLD_DB}dB:"
            f"start_silence={TTS_TRAILING_SAFETY_SECONDS},"
            "areverse"
        )
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i",
            str(src_wav),
            "-af",
            silence_filter,
            "-ar",
            str(settings.DUB_SAMPLE_RATE),
            "-ac",
            "1",
            str(out_wav),
        ]
        self._run_ffmpeg(cmd, "trim_outer_silence")
        return out_wav

    @staticmethod
    def _natural_timeline_end(
        segments: List,
        durations: List[float],
        tempo_ratio: float,
        sentence_gap: float = NATURAL_SENTENCE_GAP_SECONDS,
        total_duration: float | None = None,
        lead_seconds: float = 0.0,
    ) -> float:
        cursor = 0.0
        has_speech = False
        last_index = len(segments) - 1
        for index, (segment, duration) in enumerate(zip(segments, durations)):
            earliest = cursor + (sentence_gap if has_speech else 0.0)
            preferred_start = max(0.0, float(segment.start) - lead_seconds)
            if total_duration is not None and index == last_index:
                latest_fitting_start = total_duration - (duration / tempo_ratio)
                preferred_start = max(
                    0.0,
                    float(segment.start) - NATURAL_MAX_FINAL_LEAD_SECONDS,
                    min(preferred_start, latest_fitting_start),
                )
            actual_start = max(preferred_start, earliest)
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
        *, provider: str | None = None,
    ) -> Path:
        """Build a natural, non-overlapping timeline without per-line trimming."""
        fitted_dir = temp_dir / "fitted"
        fitted_dir.mkdir(parents=True, exist_ok=True)
        list_file = temp_dir / "concat_list.txt"
        entries: List[str] = []

        prepared_dir = temp_dir / "prepared"
        prepared_dir.mkdir(parents=True, exist_ok=True)
        prepared_paths: List[Path] = []
        for index, raw in enumerate(raw_paths):
            if not raw.is_file():
                prepared_paths.append(raw)
                continue
            prepared = prepared_dir / f"seg_{index:06d}.wav"
            try:
                self._trim_outer_silence(raw, prepared)
                if prepared.is_file() and self._probe_duration(prepared) > 0.05:
                    prepared_paths.append(prepared)
                    continue
            except Exception as exc:
                logger.warning("Failed to trim generated outer silence for segment %s: %s", index, exc)
            prepared_paths.append(raw)

        durations = [self._probe_duration(raw) for raw in prepared_paths]
        sentence_gap = NATURAL_SENTENCE_GAP_SECONDS
        max_uniform_tempo = (
            NATURAL_MAX_UNIFORM_TEMPO
            if provider == "qwen"
            else NATURAL_MAX_ADJUSTABLE_TEMPO
        )
        natural_end = self._natural_timeline_end(
            segments, durations, 1.0, sentence_gap, total_duration
        )
        timeline_tempo = 1.0
        lead_seconds = 0.0
        if natural_end > total_duration + 0.01:
            led_end = self._natural_timeline_end(
                segments,
                durations,
                1.0,
                sentence_gap,
                total_duration,
                NATURAL_MAX_UTTERANCE_LEAD_SECONDS,
            )
            if led_end <= total_duration + 0.01:
                low_lead, high_lead = 0.0, NATURAL_MAX_UTTERANCE_LEAD_SECONDS
                for _ in range(16):
                    middle_lead = (low_lead + high_lead) / 2
                    if self._natural_timeline_end(
                        segments,
                        durations,
                        1.0,
                        sentence_gap,
                        total_duration,
                        middle_lead,
                    ) > total_duration:
                        low_lead = middle_lead
                    else:
                        high_lead = middle_lead
                lead_seconds = high_lead
            else:
                lead_seconds = NATURAL_MAX_UTTERANCE_LEAD_SECONDS
        if self._natural_timeline_end(
            segments,
            durations,
            timeline_tempo,
            sentence_gap,
            total_duration,
            lead_seconds,
        ) > total_duration + 0.01:
            fastest_end = self._natural_timeline_end(
                segments,
                durations,
                max_uniform_tempo,
                sentence_gap,
                total_duration,
                lead_seconds,
            )
            if fastest_end > total_duration + 0.01:
                tightest_end = self._natural_timeline_end(
                    segments,
                    durations,
                    max_uniform_tempo,
                    NATURAL_MIN_SENTENCE_GAP_SECONDS,
                    total_duration,
                    lead_seconds,
                )
                if tightest_end > total_duration + 0.01:
                    overflow = tightest_end - total_duration
                    advice = (
                        "Qwen 当前固定为 1.00x 合成语速。请精简目标语言译文后重试，"
                        "或在设置中选择支持调整语速的配音引擎。"
                        if provider == "qwen" else "请精简译文或提高配音语速后重试。"
                    )
                    raise ValueError(
                        "自然配音内容过长，即使统一加速到 "
                        f"{max_uniform_tempo:.2f}x 仍超出视频 {overflow:.2f} 秒。"
                        + advice
                    )
                low_gap, high_gap = NATURAL_MIN_SENTENCE_GAP_SECONDS, sentence_gap
                for _ in range(16):
                    middle_gap = (low_gap + high_gap) / 2
                    if self._natural_timeline_end(
                        segments,
                        durations,
                        max_uniform_tempo,
                        middle_gap,
                        total_duration,
                        lead_seconds,
                    ) <= total_duration:
                        low_gap = middle_gap
                    else:
                        high_gap = middle_gap
                sentence_gap = low_gap
                timeline_tempo = max_uniform_tempo
            else:
                low, high = 1.0, max_uniform_tempo
                for _ in range(16):
                    middle = (low + high) / 2
                    if self._natural_timeline_end(
                        segments,
                        durations,
                        middle,
                        sentence_gap,
                        total_duration,
                        lead_seconds,
                    ) > total_duration:
                        low = middle
                    else:
                        high = middle
                timeline_tempo = high
        logger.info(
            "Dubbing natural timeline uses one uniform tempo %.3fx, %.3fs sentence gaps, "
            "and up to %.3fs distributed lead (groups=%s, natural_end=%.3fs, video=%.3fs)",
            timeline_tempo,
            sentence_gap,
            lead_seconds,
            len(segments),
            natural_end,
            total_duration,
        )

        cursor = 0.0
        has_speech = False
        last_index = len(segments) - 1
        for i, (seg, raw, raw_duration) in enumerate(zip(segments, prepared_paths, durations)):
            earliest = cursor + (sentence_gap if has_speech else 0.0)
            preferred_start = max(0.0, float(seg.start) - lead_seconds)
            if i == last_index:
                latest_fitting_start = total_duration - (raw_duration / timeline_tempo)
                preferred_start = max(
                    0.0,
                    float(seg.start) - NATURAL_MAX_FINAL_LEAD_SECONDS,
                    min(preferred_start, latest_fitting_start),
                )
            actual_start = max(preferred_start, earliest)
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
        if clone_sample_path and normalize_tts_mode(settings.TTS_MODE) in {"kokoro", "cosyvoice", "qwen"}:
            raise ValueError(
                "当前本地 TTS Provider 不接受用户克隆样本。"
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
            raise ValueError("Edge 在线配音不支持语音克隆，请使用标准预置音色。")

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
            probed_video_duration = self._probe_duration(video_path_obj)
            total_duration = (
                probed_video_duration
                if probed_video_duration > 0
                else max(float(segments[-1].end), 0.1)
            )

            # Stage B: synthesize each segment
            logger.info(f"Dubbing task {task_id}: {len(segments)} segments, voice={effective_voice}")
            raw_paths = asyncio.run(
                self._synthesize_all(
                    dubbing_segments, effective_voice, speed, task_id, temp_dir, provider,
                    getattr(result, "language", None) or lang_label,
                )
            )

            # Stage C: align + build full track
            emit_event(
                task_id,
                {"status": "dubbing", "message": "正在对齐时间轴...", "progress": 70},
            )
            dub_track = self._build_timeline(
                dubbing_segments, raw_paths, total_duration, temp_dir, provider=provider
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
