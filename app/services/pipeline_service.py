"""Integrated ASR -> translation -> dubbing state machine."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import unquote

from app.core.config import settings
from app.core.events import emit_event
from app.core.history import history_manager

logger = logging.getLogger(__name__)


class PipelineService:
    def __init__(self, *, transcribe_stage: Callable | None = None, translate_stage: Callable | None = None, export_stage: Callable | None = None, dub_stage: Callable | None = None, burn_stage: Callable | None = None):
        self._transcribe_stage = transcribe_stage
        self._translate_stage = translate_stage
        self._export_stage = export_stage
        self._dub_stage = dub_stage
        self._burn_stage = burn_stage
        self._tasks: dict[str, asyncio.Task] = {}

    def _set_stage(self, task_id: str, stage: str, message: str, progress: int) -> None:
        data = {
            "status": stage,
            "pipeline_status": "processing",
            "pipeline_stage": stage,
            "message": message,
            "progress_percent": progress,
        }
        history_manager.update_task(task_id, data)
        emit_event(task_id, data)

    @staticmethod
    def _error_message(exc: Exception, task: dict | None = None) -> str:
        detail = getattr(exc, "detail", None)
        if isinstance(detail, str) and detail.strip():
            return detail
        message = str(exc).strip()
        if message:
            return message
        persisted = str((task or {}).get("message") or "").strip()
        return persisted or exc.__class__.__name__

    @staticmethod
    def _artifact_exists(value: str | None) -> bool:
        if not value:
            return False
        if value.startswith("/static/output/"):
            return (Path(settings.OUTPUT_DIR) / Path(unquote(value)).name).exists()
        return Path(value).exists()

    @staticmethod
    def _artifact_path(value: str | None) -> Path | None:
        if not value:
            return None
        if value.startswith("/static/output/"):
            return Path(settings.OUTPUT_DIR) / Path(unquote(value)).name
        return Path(value)

    @classmethod
    def _can_reuse_dubbing(
        cls, task: dict, target_lang: str, voice: str, speed: float
    ) -> bool:
        return (
            task.get("dubbing_status") == "completed"
            and task.get("dubbing_target_lang") == target_lang
            and task.get("dubbing_voice") == voice
            and abs(float(task.get("dubbing_speed", -1)) - float(speed)) < 0.0001
            and cls._artifact_exists(task.get("dubbing_audio_path"))
            and cls._artifact_exists(task.get("dubbing_raw_video_path") or task.get("dubbing_video_path"))
        )

    async def _default_transcribe(self, task_id: str, task: dict) -> None:
        from app.api.transcribe import run_transcription_task
        source = task.get("source_path") or task.get("upload_path")
        await run_transcription_task(task_id, source, task.get("filename", Path(source).name), task.get("language"))
        updated = history_manager.get_task(task_id) or {}
        if not updated.get("transcription_path"):
            raise RuntimeError(updated.get("message") or "Transcription did not produce an output")

    async def _default_translate(self, task_id: str, target_lang: str) -> None:
        from app.api.translation import translate_transcription
        task = history_manager.get_task(task_id) or {}
        await translate_transcription(
            json_path=task.get("transcription_path", ""), target_lang=target_lang,
            task_id=task_id, pipeline=True,
        )

    async def _default_export(self, task_id: str, target_lang: str, subtitle_format: str) -> None:
        import json
        from app.core.schemas import TranscriptionResult
        from app.services.formatter_service import subtitle_formatter

        task = history_manager.get_task(task_id) or {}
        translation_path = (task.get("translations") or {}).get(target_lang)
        if not translation_path or not Path(translation_path).exists():
            raise RuntimeError("Translation did not produce an exportable subtitle source")
        with open(translation_path, "r", encoding="utf-8") as source:
            result = TranscriptionResult(**json.load(source))
        ext = subtitle_format.lower()
        content = {
            "srt": subtitle_formatter.to_srt,
            "vtt": subtitle_formatter.to_vtt,
            "ass": subtitle_formatter.to_ass,
        }[ext](result)
        output_name = f"{Path(translation_path).stem}_{task_id}"
        output_path = subtitle_formatter.save_to_file(content, output_name, ext)
        subtitle_outputs = dict(task.get("subtitle_outputs") or {})
        subtitle_outputs[ext] = str(output_path)
        history_manager.update_task(
            task_id,
            {"subtitle_outputs": subtitle_outputs, "last_subtitle_format": ext},
        )

    async def _default_dub(self, task_id: str, target_lang: str, voice: str, speed: float) -> None:
        from app.services.dubbing_service import dubbing_service
        await asyncio.to_thread(dubbing_service.dub, task_id, target_lang, voice, speed, None)

    async def _default_burn(
        self,
        task_id: str,
        target_lang: str,
        show_source: bool,
        show_target: bool,
        subtitle_style: dict[str, Any],
        video_path: Path,
    ) -> None:
        from app.services.video_burn_service import video_burn_service
        await asyncio.to_thread(
            video_burn_service.burn,
            task_id,
            show_source,
            show_target,
            subtitle_style,
            target_lang,
            str(video_path),
            "dubbing_video_path",
        )

    async def run(
        self,
        task_id: str,
        target_lang: str,
        voice: str,
        speed: float = 1.0,
        burn_subtitles: bool = False,
        show_source: bool = False,
        show_target: bool = True,
        subtitle_style: dict[str, Any] | None = None,
        subtitle_format: str = "srt",
        force_translate: bool = False,
        force_dub: bool = False,
    ) -> None:
        try:
            task = history_manager.get_task(task_id)
            if not task:
                raise RuntimeError("Task not found")

            if not task.get("transcription_path") or not Path(task["transcription_path"]).exists():
                self._set_stage(task_id, "pipeline_transcribing", "正在转写...", 5)
                stage = self._transcribe_stage or self._default_transcribe
                await stage(task_id, task)

            task = history_manager.get_task(task_id) or {}
            translation_path = (task.get("translations") or {}).get(target_lang)
            translation_profile = (task.get("translation_profiles") or {}).get(target_lang)
            if (
                force_translate
                or not translation_path
                or not Path(translation_path).exists()
                or translation_profile != "dubbing"
            ):
                self._set_stage(task_id, "pipeline_translating", "正在翻译...", 40)
                stage = self._translate_stage or self._default_translate
                await stage(task_id, target_lang)

            task = history_manager.get_task(task_id) or {}
            subtitle_path = (task.get("subtitle_outputs") or {}).get(subtitle_format)
            if task.get("translation_path") and (
                force_translate or not self._artifact_exists(subtitle_path)
            ):
                stage = self._export_stage or self._default_export
                await stage(task_id, target_lang, subtitle_format)

            task = history_manager.get_task(task_id) or {}
            if force_translate or force_dub or not self._can_reuse_dubbing(task, target_lang, voice, speed):
                self._set_stage(task_id, "pipeline_dubbing", "正在配音并合并视频...", 75)
                stage = self._dub_stage or self._default_dub
                await stage(task_id, target_lang, voice, speed)

            task = history_manager.get_task(task_id) or {}
            raw_video_value = task.get("dubbing_raw_video_path") or task.get("dubbing_video_path")
            raw_video = self._artifact_path(raw_video_value)
            if not raw_video or not raw_video.exists():
                raise RuntimeError("Dubbing did not produce a video output")

            if burn_subtitles:
                self._set_stage(task_id, "pipeline_rendering", "正在把字幕烧录到配音视频...", 90)
                stage = self._burn_stage or self._default_burn
                await stage(
                    task_id,
                    target_lang,
                    show_source,
                    show_target,
                    subtitle_style or {},
                    raw_video,
                )
                history_manager.update_task(task_id, {"dubbing_burn_subtitles": True})
            else:
                history_manager.update_task(
                    task_id,
                    {
                        "dubbing_video_path": raw_video_value,
                        "dubbing_burn_subtitles": False,
                    },
                )

            data = {
                "status": "completed", "pipeline_status": "completed",
                "pipeline_stage": "completed", "message": "完整流水线处理完成",
                "progress_percent": 100,
                "failed_stage": None,
                "cancelled_stage": None,
            }
            history_manager.update_task(task_id, data)
            emit_event(task_id, data)
        except asyncio.CancelledError:
            data = {
                "status": "cancelled", "pipeline_status": "cancelled",
                "message": "流水线已取消", "cancelled_stage": (history_manager.get_task(task_id) or {}).get("pipeline_stage"),
            }
            history_manager.update_task(task_id, data)
            emit_event(task_id, data)
            raise
        except Exception as exc:
            logger.exception("Pipeline failed for task %s", task_id)
            task = history_manager.get_task(task_id) or {}
            data = {
                "status": "failed", "pipeline_status": "failed",
                "message": self._error_message(exc, task),
                "failed_stage": task.get("pipeline_stage"),
            }
            history_manager.update_task(task_id, data)
            emit_event(task_id, data)
        finally:
            self._tasks.pop(task_id, None)

    def start(
        self,
        task_id: str,
        target_lang: str,
        voice: str,
        speed: float = 1.0,
        burn_subtitles: bool = False,
        show_source: bool = False,
        show_target: bool = True,
        subtitle_style: dict[str, Any] | None = None,
        subtitle_format: str = "srt",
        force_translate: bool = False,
        force_dub: bool = False,
    ) -> asyncio.Task:
        current = self._tasks.get(task_id)
        if current and not current.done():
            raise RuntimeError("Pipeline already running")
        task = asyncio.create_task(
            self.run(
                task_id,
                target_lang,
                voice,
                speed,
                burn_subtitles,
                show_source,
                show_target,
                subtitle_style,
                subtitle_format,
                force_translate,
                force_dub,
            ),
            name=f"pipeline-{task_id}",
        )
        self._tasks[task_id] = task
        return task

    async def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.done():
            return False
        from app.services.dubbing_service import dubbing_service
        dubbing_was_active = dubbing_service.request_cancel(task_id)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if dubbing_was_active:
            stopped = await asyncio.to_thread(dubbing_service.wait_for_stop, task_id, 20)
            if not stopped:
                logger.error("Dubbing thread did not stop within cancellation timeout for %s", task_id)
        return True

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.cancel(task_id) for task_id in list(self._tasks)), return_exceptions=True)


pipeline_service = PipelineService()
