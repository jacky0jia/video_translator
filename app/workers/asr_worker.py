"""One-process-per-stage faster-whisper worker."""
from __future__ import annotations

import asyncio
import multiprocessing
import traceback
from pathlib import Path

from app.core.schemas import TranscriptionResult, TranscriptionSegment


def _worker_main(connection, model_args, transcribe_args):
    model = None
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(**model_args)
        connection.send({"ok": True, "type": "ready"})
        while True:
            command = connection.recv()
            if command["type"] == "shutdown":
                break
            if command["type"] != "transcribe":
                raise RuntimeError(f"Unknown ASR worker command: {command['type']}")
            segments, info = model.transcribe(
                command["audio_path"], language=command.get("language"), **transcribe_args
            )
            connection.send({
                "ok": True,
                "type": "result",
                "language": info.language,
                "language_probability": info.language_probability,
                "segments": [
                    {
                        "start": segment.start, "end": segment.end,
                        "text": segment.text.strip(), "confidence": segment.avg_logprob,
                    }
                    for segment in segments
                ],
            })
    except BaseException as exc:
        try:
            connection.send({"ok": False, "error": str(exc), "traceback": traceback.format_exc()})
        except Exception:
            pass
    finally:
        if model is not None:
            try:
                model.model.unload_model()
            except Exception:
                pass
        connection.close()


class ASRWorkerClient:
    def __init__(self, *, startup_timeout: float = 120, shutdown_timeout: float = 10, worker_target=None):
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self._process = None
        self._connection = None
        self._worker_target = worker_target or _worker_main

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.is_alive())

    async def start(self, *, model_args: dict, transcribe_args: dict) -> None:
        if self.running:
            raise RuntimeError("ASR worker is already running")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=self._worker_target, args=(child, model_args, transcribe_args),
            name="subtitle-translator-asr", daemon=True,
        )
        process.start()
        child.close()
        self._process, self._connection = process, parent
        try:
            response = await self._receive(self.startup_timeout)
        except BaseException:
            await self.close()
            raise
        if not response.get("ok"):
            await self.close()
            raise RuntimeError(f"ASR worker startup failed: {response.get('error')}")

    async def _receive(self, timeout: float) -> dict:
        connection = self._connection
        if connection is None:
            raise RuntimeError("ASR worker is not connected")

        def receive():
            if not connection.poll(timeout):
                raise TimeoutError(f"ASR worker timed out after {timeout:g}s")
            try:
                return connection.recv()
            except EOFError as exc:
                code = self._process.exitcode if self._process else None
                raise RuntimeError(f"ASR worker exited unexpectedly (code={code})") from exc

        return await asyncio.to_thread(receive)

    async def transcribe(self, audio_path: Path, language: str | None) -> TranscriptionResult:
        if not self.running or self._connection is None:
            raise RuntimeError("ASR worker is not running")
        self._connection.send({"type": "transcribe", "audio_path": str(audio_path), "language": language})
        response = await self._receive(600)
        if not response.get("ok"):
            raise RuntimeError(f"ASR worker failed: {response.get('error')}")
        return TranscriptionResult(
            video_source=str(audio_path), language=response["language"],
            segments=[TranscriptionSegment(**segment) for segment in response["segments"]],
        )

    async def close(self) -> None:
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if not process:
            return
        if process.is_alive() and connection:
            try:
                connection.send({"type": "shutdown"})
            except (BrokenPipeError, EOFError, OSError):
                pass
            await asyncio.to_thread(process.join, self.shutdown_timeout)
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, self.shutdown_timeout)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, self.shutdown_timeout)
        if connection:
            connection.close()
        process.close()
