"""Online Edge TTS provider for Chinese, English, Japanese, and Korean dubbing."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import struct
import time
from typing import Callable

logger = logging.getLogger(__name__)

_SUPPORTED_LOCALES = {"zh": "zh", "en": "en", "ja": "ja", "ko": "ko"}


class EdgeTTSService:
    _voice_cache = None
    _voice_cache_at = 0.0
    def __init__(self, ffmpeg_path: str, *, sample_rate: int = 22050, communicator_factory: Callable | None = None, retries: int = 2):
        self.ffmpeg_path = ffmpeg_path
        self.sample_rate = sample_rate
        self._communicator_factory = communicator_factory
        self.retries = retries

    @staticmethod
    def _rate(speed: float) -> str:
        percent = round((speed - 1.0) * 100)
        return f"{percent:+d}%"

    def _communicator(self, text: str, voice: str, speed: float):
        if self._communicator_factory:
            return self._communicator_factory(text, voice, self._rate(speed))
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("Edge TTS requires the edge-tts package") from exc
        return edge_tts.Communicate(text, voice, rate=self._rate(speed))

    def _mp3_to_wav(self, audio: bytes) -> bytes:
        result = subprocess.run(
            [self.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ar", str(self.sample_rate), "-ac", "1", "-f", "wav", "pipe:1"],
            input=audio, capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Edge audio conversion failed: {result.stderr.decode(errors='replace')[-400:]}")
        wav = bytearray(result.stdout)
        # FFmpeg cannot seek on stdout, so it writes 0xFFFFFFFF length
        # placeholders. Patch them after capture or duration probes interpret
        # a short clip as many hours long.
        if len(wav) < 44 or wav[:4] != b"RIFF":
            raise RuntimeError("Edge audio conversion returned an invalid WAV")
        struct.pack_into("<I", wav, 4, len(wav) - 8)
        data_tag = wav.find(b"data", 12)
        if data_tag < 0 or data_tag + 8 > len(wav):
            raise RuntimeError("Edge WAV has no data chunk")
        struct.pack_into("<I", wav, data_tag + 4, len(wav) - data_tag - 8)
        return bytes(wav)

    async def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                chunks = []
                async for chunk in self._communicator(text, voice, speed).stream():
                    if chunk.get("type") == "audio":
                        chunks.append(chunk["data"])
                if not chunks:
                    raise RuntimeError("Edge TTS returned no audio")
                return await asyncio.to_thread(self._mp3_to_wav, b"".join(chunks))
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(attempt + 1)
        raise RuntimeError(
            f"Edge TTS online synthesis failed after {self.retries + 1} attempts. "
            f"Check the internet connection and Microsoft Edge speech service: {last_error}"
        )

    async def list_voices(self) -> list[dict]:
        if self._voice_cache is not None and time.monotonic() - self._voice_cache_at < 3600:
            return list(self._voice_cache)
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("Edge TTS requires the edge-tts package") from exc
        voices = await edge_tts.list_voices()
        result = [
            {
                "id": voice["ShortName"],
                "name": voice.get("FriendlyName", voice["ShortName"]),
                "language": str(voice.get("Locale", "")).lower().split("-", 1)[0],
                "gender": str(voice.get("Gender", "")).lower(),
                "provider": "edge",
                "online": True,
            }
            for voice in voices
            if str(voice.get("Locale", "")).lower().split("-", 1)[0] in _SUPPORTED_LOCALES
        ]
        type(self)._voice_cache = result
        type(self)._voice_cache_at = time.monotonic()
        return list(result)
