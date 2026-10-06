"""Bounded duration inspection without decoding the media timeline."""
import json
import logging
import math
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path

logger = logging.getLogger(__name__)
PROBE_TIMEOUT_SECONDS = 15


def _positive_duration(value):
    try:
        duration = float(value)
        return duration if math.isfinite(duration) and duration > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def probe_media_duration(path: Path, ffmpeg_path: str) -> float:
    """Read WAV headers or container metadata; never decode the entire file."""
    path = Path(path)
    started = time.monotonic()
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                return audio.getnframes() / audio.getframerate()
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            pass  # Non-PCM WAVs can still be inspected by ffprobe.
    parent = Path(ffmpeg_path).parent
    probe = next((str(candidate) for candidate in
                  (parent / "ffprobe.exe", parent / "ffprobe") if candidate.is_file()), None)
    probe = probe or shutil.which("ffprobe")
    if probe:
        try:
            result = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration:stream=duration",
                 "-of", "json", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=PROBE_TIMEOUT_SECONDS, check=True,
            )
            data = json.loads(result.stdout)
            duration = _positive_duration((data.get("format") or {}).get("duration"))
            if not duration:
                duration = max((_positive_duration(stream.get("duration"))
                                for stream in data.get("streams", [])), default=0.0)
            if duration:
                logger.info("Media duration metadata: %.3fs, inspected in %.3fs", duration,
                            time.monotonic() - started)
                return duration
        except subprocess.TimeoutExpired:
            logger.warning("Media duration inspection timed out after %ss", PROBE_TIMEOUT_SECONDS)
            return 0.0
        except (OSError, ValueError, TypeError, AttributeError, subprocess.CalledProcessError) as exc:
            logger.warning("ffprobe duration metadata unavailable: %s", exc)
    # Input inspection without an output: FFmpeg prints headers then exits.
    # Do not add '-f null -': that would decode every video/audio frame.
    try:
        result = subprocess.run(
            [str(ffmpeg_path), "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        match = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.\d+)", result.stderr or "")
        if match:
            hours, minutes, seconds = map(float, match.groups())
            return _positive_duration(hours * 3600 + minutes * 60 + seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("FFmpeg duration metadata unavailable: %s", exc)
    return 0.0
