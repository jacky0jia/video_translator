import logging
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)


class AudioExtractionService:
    def __init__(self):
        self.ffmpeg_path = settings.ffmpeg_path

    def _verify_ffmpeg(self):
        if not self.ffmpeg_path:
            raise RuntimeError(
                "FFmpeg executable not found. Please install FFmpeg or place ffmpeg.exe in ffmpeg/ or bin/ (Windows)."
            )

    async def extract_audio(self, input_source: str | UploadFile) -> Path:
        """
        Extracts audio from a video source and returns the path to the temporary wav file.
        Supports both local paths and FastAPI UploadFile objects.
        """
        self._verify_ffmpeg()
        settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        temp_input = None
        output_audio = None
        succeeded = False
        try:
            # Atomically reserve invocation-owned names, even for the same source.
            # Never use the untrusted upload filename as a filesystem path.
            if isinstance(input_source, UploadFile):
                descriptor, name = tempfile.mkstemp(
                    prefix="input_", suffix=".media", dir=settings.TEMP_DIR
                )
                temp_input = Path(name)
                with os.fdopen(descriptor, "wb") as buffer:
                    while content := await input_source.read(1024 * 1024):
                        buffer.write(content)
                source_path = str(temp_input)
            else:
                source_path = input_source

            descriptor, name = tempfile.mkstemp(
                prefix="extracted_", suffix=".wav", dir=settings.TEMP_DIR
            )
            output_audio = Path(name)
            os.close(descriptor)

            # Using subprocess directly for better control over the bundled ffmpeg path
            # Command: ffmpeg -i [input] -vn -acodec pcm_s16le -ar 16000 -ac 1 [output]
            # -vn: disable video
            # -acodec pcm_s16le: 16-bit PCM
            # -ar 16000: 16kHz sample rate (required by most ASR models)
            # -ac 1: mono channel
            # Resolve to absolute path to prevent option injection via filenames starting with '-'
            safe_source = str(Path(source_path).resolve())
            cmd = [
                self.ffmpeg_path,
                "-y",  # overwrite output files
                "-i",
                safe_source,
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(output_audio),
            ]

            logger.info(f"Running FFmpeg command: {' '.join(cmd)}")
            subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
            )
            succeeded = True
            return output_audio

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {e.stderr}")
            raise RuntimeError(f"Failed to extract audio: {e.stderr}")
        finally:
            # Never remove another invocation's input or successful audio.
            owned_paths = [temp_input]
            if not succeeded:
                owned_paths.append(output_audio)
            for owned_path in owned_paths:
                if owned_path is None:
                    continue
                try:
                    owned_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Failed to clean extraction temporary file %s: %s", owned_path, exc)

    async def cleanup_audio(self, audio_path: Path):
        """Deletes the temporary audio file."""
        try:
            if audio_path.exists():
                os.remove(audio_path)
        except OSError as e:
            logger.error(f"Error cleaning up audio file {audio_path}: {e}")


audio_service = AudioExtractionService()
