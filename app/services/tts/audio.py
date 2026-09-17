"""Validation helpers for the normalized provider audio boundary."""

import audioop
import io
import wave

from app.services.tts.base import TTSSynthesisError, TTSSynthesisResult


def normalized_wav_result(audio: bytes, expected_sample_rate: int) -> TTSSynthesisResult:
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            frames = wav.getnframes()
            frame_data = wav.readframes(frames)
    except (EOFError, wave.Error) as exc:
        raise TTSSynthesisError("TTS provider returned an invalid WAV file") from exc
    if channels != 1 or sample_width != 2:
        raise TTSSynthesisError("TTS provider must return mono PCM-16 WAV audio")
    if sample_rate != expected_sample_rate:
        try:
            frame_data, _ = audioop.ratecv(
                frame_data,
                sample_width,
                channels,
                sample_rate,
                expected_sample_rate,
                None,
            )
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(channels)
                wav.setsampwidth(sample_width)
                wav.setframerate(expected_sample_rate)
                wav.writeframes(frame_data)
            audio = output.getvalue()
            sample_rate = expected_sample_rate
            frames = len(frame_data) // (sample_width * channels)
        except (audioop.error, wave.Error) as exc:
            raise TTSSynthesisError(
                f"TTS provider audio could not be resampled to {expected_sample_rate} Hz"
            ) from exc
    return TTSSynthesisResult(
        audio=audio,
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=frames / sample_rate if sample_rate else None,
    )
