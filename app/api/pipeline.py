from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.history import history_manager
from app.services.pipeline_service import pipeline_service
from app.core.config import settings
from app.services.tts.registry import is_korean_language, resolve_tts_route
from app.services.tts.voice_selection import resolve_available_voice
from app.services.dubbing_service import dubbing_service

router = APIRouter()


class PipelineRequest(BaseModel):
    task_id: str
    target_lang: str = "Chinese"
    voice: str = "af_heart"
    speed: float = 1.0
    subtitle_format: str = "srt"
    burn_subtitles: bool = False
    show_source: bool = False
    show_target: bool = True
    subtitle_style: dict = Field(default_factory=dict)
    force_translate: bool = False
    force_dub: bool = False


@router.post("/pipeline")
async def start_pipeline(request: PipelineRequest):
    if not history_manager.get_task(request.task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    if not 0.25 <= request.speed <= 4.0:
        raise HTTPException(status_code=400, detail="speed must be in [0.25, 4.0]")
    if request.subtitle_format.lower() not in {"srt", "vtt", "ass"}:
        raise HTTPException(status_code=400, detail="subtitle_format must be srt, vtt, or ass")
    route = resolve_tts_route(settings.TTS_MODE, request.target_lang)
    is_korean = is_korean_language(request.target_lang)
    if route.provider_id == "kokoro" and is_korean:
        raise HTTPException(
            status_code=400,
            detail="Kokoro 不支持韩语配音；请选择中文、英文或日文，或切换到 Edge/Qwen。",
        )
    if route.provider_id == "kokoro" and not 0.5 <= request.speed <= 2.0:
        raise HTTPException(status_code=400, detail="Kokoro speed must be in [0.5, 2.0]")
    if route.provider_id in {"cosyvoice", "qwen"} and request.speed != 1.0:
        raise HTTPException(status_code=400, detail="Local cloned-voice generation speed must be 1.0")
    voice = request.voice
    if route.provider_id in {"kokoro", "cosyvoice", "qwen", "edge"}:
        try:
            voice = await resolve_available_voice(
                dubbing_service.tts_registry, route, voice, request.target_lang
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        history_manager.update_task(
            request.task_id,
            {
                "last_process_route": "dubbing",
                "last_subtitle_format": request.subtitle_format.lower(),
                "dubbing_burn_subtitles": request.burn_subtitles,
            },
        )
        pipeline_service.start(
            request.task_id,
            request.target_lang,
            voice,
            request.speed,
            request.burn_subtitles,
            request.show_source,
            request.show_target,
            request.subtitle_style,
            request.subtitle_format.lower(),
            request.force_translate,
            request.force_dub,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "started", "task_id": request.task_id}


@router.post("/pipeline/{task_id}/cancel")
async def cancel_pipeline(task_id: str):
    if not await pipeline_service.cancel(task_id):
        raise HTTPException(status_code=409, detail="Pipeline is not running")
    return {"status": "cancelled", "task_id": task_id}
