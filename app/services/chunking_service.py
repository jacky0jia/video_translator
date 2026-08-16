import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


def deduplicate_segments(segments):
    """Remove near-duplicate segments in overlap regions, keeping higher confidence."""
    segments = sorted(segments, key=lambda s: s.start)
    result = []
    for seg in segments:
        if result and abs(seg.start - result[-1].start) < settings.DEDUPLICATE_THRESHOLD_SECONDS:
            if seg.confidence > result[-1].confidence:
                result[-1] = seg
        else:
            result.append(seg)
    return result


class AudioChunkingService:
    def __init__(self):
        self.ffmpeg_path = settings.ffmpeg_path
        self.ffprobe_path = self._resolve_ffprobe()

    def _resolve_ffprobe(self) -> str:
        """Find ffprobe next to ffmpeg, or in system PATH."""
        ffmpeg = Path(self.ffmpeg_path)
        if ffmpeg.exists():
            candidate = ffmpeg.parent / ("ffprobe.exe" if ffmpeg.name == "ffmpeg.exe" else "ffprobe")
            if candidate.exists():
                return str(candidate)

        system_ffprobe = shutil.which("ffprobe")
        if system_ffprobe:
            return system_ffprobe

        return ""

    def get_audio_duration(self, audio_path: Path) -> float:
        """Return audio duration in seconds using ffprobe."""
        if not self.ffprobe_path:
            raise RuntimeError("ffprobe not found. Please install FFmpeg or place ffprobe in bin/.")

        # Resolve to absolute path to prevent option injection via filenames starting with '-'
        safe_path = str(audio_path.resolve())
        cmd = [
            self.ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            safe_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        return float(result.stdout.strip())

    def split_audio(
        self, audio_path: Path, chunk_duration_minutes: int, overlap_seconds: int
    ) -> List[Tuple[Path, float]]:
        """
        Split audio into overlapping chunks.
        Returns list of (chunk_path, start_offset_seconds).
        """
        duration = self.get_audio_duration(audio_path)
        chunk_duration = chunk_duration_minutes * 60
        overlap = overlap_seconds

        chunks: List[Tuple[Path, float]] = []
        start = 0.0
        idx = 0

        while start < duration:
            output_path = settings.TEMP_DIR / f"chunk_{audio_path.stem}_{idx:03d}.wav"
            actual_duration = min(chunk_duration, duration - start)
            if actual_duration <= 0:
                break

            # Resolve to absolute path to prevent option injection via filenames starting with '-'
            safe_input = str(audio_path.resolve())
            cmd = [
                self.ffmpeg_path,
                "-y",
                "-i", safe_input,
                "-ss", str(start),
                "-t", str(actual_duration),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", str(settings.AUDIO_SAMPLE_RATE),
                "-ac", str(settings.AUDIO_CHANNELS),
                str(output_path),
            ]

            logger.info(f"Extracting chunk {idx}: start={start:.2f}s, duration={actual_duration:.2f}s")
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)

            chunks.append((output_path, start))
            start += chunk_duration - overlap
            idx += 1

        return chunks

    def cleanup_chunks(self, chunk_paths: List[Path]):
        """Delete temporary chunk files."""
        for path in chunk_paths:
            try:
                if path.exists():
                    path.unlink()
                    logger.debug(f"Cleaned up chunk: {path}")
            except OSError as e:
                logger.error(f"Failed to cleanup chunk {path}: {e}")


chunking_service = AudioChunkingService()
