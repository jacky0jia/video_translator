import logging
import os
import subprocess
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

        # 1. Resolve input path
        if isinstance(input_source, UploadFile):
            # Save upload to temp file first
            temp_input = settings.TEMP_DIR / f"input_{input_source.filename}"
            settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
            with open(temp_input, "wb") as buffer:
                # Read in chunks to avoid memory overflow for large videos
                while content := await input_source.read(1024 * 1024):
                    buffer.write(content)
            source_path = str(temp_input)
            is_temporary_input = True
        else:
            source_path = input_source
            is_temporary_input = False

        # 2. Define output path
        output_audio = settings.TEMP_DIR / f"extracted_{Path(source_path).stem}.wav"
        settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)

        try:
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
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
            )

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error: {e.stderr}")
            raise RuntimeError(f"Failed to extract audio: {e.stderr}")
        finally:
            # Clean up the temporary input file if it was an upload
            if is_temporary_input:
                try:
                    os.remove(source_path)
                except OSError:
                    pass

        return output_audio

    async def cleanup_audio(self, audio_path: Path):
        """Deletes the temporary audio file."""
        try:
            if audio_path.exists():
                os.remove(audio_path)
        except OSError as e:
            logger.error(f"Error cleaning up audio file {audio_path}: {e}")


audio_service = AudioExtractionService()
