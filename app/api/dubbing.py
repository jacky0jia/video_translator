"""Dubbing API routes. Mirrors the structure of `app/api/burn.py`."""
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.core.history import history_manager
from app.services.dubbing_service import dubbing_service
from app.services.dubbing_service import _is_kokoro_mode
from app.services.tts.registry import is_korean_language, resolve_tts_route
from app.services.tts.voice_selection import resolve_available_voice

logger = logging.getLogger(__name__)
router = APIRouter()


class DubbingRequest(BaseModel):
    task_id: str
    target_lang: Optional[str] = None  # which subtitle language to dub; None = translated first
    voice: str  # preset voice id or clone voice name
    speed: float = 1.0
    clone_sample_path: Optional[str] = None  # server path of uploaded clone sample


def _requested_language(task: dict, target_lang: Optional[str]) -> str:
    value = task.get("language") if target_lang == "source" else target_lang
    if not value:
        value = task.get("target_lang") or ""
    return str(value).strip().lower()


def _is_korean(language: str) -> bool:
    return is_korean_language(language)


def _run_dubbing_task(
    task_id: str,
    target_lang: Optional[str],
    voice: str,
    speed: float,
    clone_sample_path: Optional[str],
):
    try:
        dubbing_service.dub(task_id, target_lang, voice, speed, clone_sample_path)
    except Exception:
        logger.exception(f"Background dubbing task failed for {task_id}")
        # Error state already updated inside dub()


@router.post("/dubbing")
async def start_dubbing(request: DubbingRequest, background_tasks: BackgroundTasks):
    task = history_manager.get_task(request.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    video_path = task.get("upload_path") or task.get("source_path")
    if not video_path:
        raise HTTPException(status_code=400, detail="Source video path not found")
    if not Path(video_path).exists():
        raise HTTPException(status_code=400, detail="Source video file not found")

    # Must have some subtitle to dub
    has_subtitle = bool(
        task.get("transcription_path")
        or task.get("translation_path")
        or task.get("translations")
    )
    if not has_subtitle:
        raise HTTPException(status_code=400, detail="No subtitle available for dubbing")

    if task.get("dubbing_status") == "processing":
        raise HTTPException(status_code=409, detail="Dubbing already in progress")

    if settings.TTS_MODE == "speaches" and not settings.TTS_API_URL:
        raise HTTPException(status_code=400, detail="TTS_API_URL not configured")

    requested_language = _requested_language(task, request.target_lang)
    route = resolve_tts_route(settings.TTS_MODE, requested_language)
    voice = request.voice
    if route.provider_id == "kokoro" and _is_korean(requested_language):
        raise HTTPException(
            status_code=400,
            detail="Kokoro 不支持韩语配音；请选择中文、英文或日文，或切换到 Edge/Qwen。",
        )
    if route.provider_id == "edge":
        if request.clone_sample_path:
            raise HTTPException(status_code=400, detail="Edge 在线配音不支持语音克隆")
    if not (0.25 <= request.speed <= 4.0):
        raise HTTPException(status_code=400, detail="speed must be in [0.25, 4.0]")
    if route.provider_id == "kokoro" and not (0.5 <= request.speed <= 2.0):
        raise HTTPException(status_code=400, detail="Kokoro speed must be in [0.5, 2.0]")
    if route.provider_id in {"cosyvoice", "qwen"}:
        if request.speed != 1.0:
            raise HTTPException(status_code=400, detail="Local cloned-voice generation speed must be 1.0")
        if request.clone_sample_path:
            raise HTTPException(
                status_code=400,
                detail="当前本地 Base 模型仅支持应用预置音色，不接受用户克隆样本。",
            )

    if route.provider_id in {"kokoro", "cosyvoice", "qwen", "edge"}:
        try:
            voice = await resolve_available_voice(
                dubbing_service.tts_registry, route, voice, requested_language
            )
        except Exception as exc:
            logger.warning("Voice selection failed for %s: %s", route.provider_id, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(
        _run_dubbing_task,
        request.task_id,
        request.target_lang,
        voice,
        request.speed,
        request.clone_sample_path,
    )
    return {"status": "started", "task_id": request.task_id}


# NOTE: static path segments (/voices, /clone-sample) MUST be declared before the
# {task_id} path parameter, otherwise "voices" would be captured as a task_id.
@router.get("/dubbing/voices")
async def list_voices():
    """Return available TTS voices.

    In local mode (TTS_MODE=local) returns the static Kokoro voice catalogue
    with language/gender metadata.  In speaches mode, proxies Speaches
    /v1/audio/voices so the frontend doesn't connect directly.
    """
    registry = dubbing_service.tts_registry
    edge_voices = []
    edge_error = None
    if settings.TTS_MODE == "edge":
        try:
            edge_voices = [asdict(voice) for voice in await registry.create("edge").list_voices()]
        except Exception as exc:
            edge_error = str(exc)
            logger.warning("Failed to fetch Edge voices: %s", exc)
    if _is_kokoro_mode():
        kokoro_voices = [
            asdict(voice) for voice in await registry.create("kokoro").list_voices()
        ]
        return {
            "voices": kokoro_voices,
            "tts_mode": "kokoro",
            "supported_languages": ["Chinese", "English", "Japanese"],
            "unsupported_languages": ["ko"],
            "online_languages": [],
        }
    if settings.TTS_MODE == "edge":
        return {
            "voices": edge_voices,
            "tts_mode": "edge",
            "supported_languages": ["Chinese", "English", "Japanese", "Korean"],
            "unsupported_languages": [] if edge_voices else ["zh", "en", "ja", "ko"],
            "online_languages": ["zh", "en", "ja", "ko"],
            "privacy_notice": "配音文本会发送到 Microsoft Edge 在线语音服务。",
            "edge_error": edge_error,
        }
    if settings.TTS_MODE == "cosyvoice":
        try:
            voices = [
                asdict(voice)
                for voice in await registry.create("cosyvoice").list_voices()
            ]
            error = None
        except Exception as exc:
            logger.warning("CosyVoice bundle is unavailable: %s", exc)
            voices, error = [], str(exc)
        response = {
            "voices": voices + edge_voices,
            "tts_mode": "cosyvoice_edge",
            "unsupported_languages": [] if edge_voices else ["ko"],
            "online_languages": ["ko"],
            "privacy_notice": "韩语文本会发送到 Microsoft Edge 在线语音服务。",
            "edge_error": edge_error,
        }
        if error:
            response["error"] = error
        return response
    if settings.TTS_MODE == "qwen":
        try:
            voices = [asdict(voice) for voice in await registry.create("qwen").list_voices()]
            error = None
        except Exception as exc:
            logger.warning("Qwen3-TTS bundle is unavailable: %s", exc)
            voices, error = [], str(exc)
        response = {
            "voices": voices,
            "tts_mode": "qwen",
            "supported_languages": ["Chinese", "English", "Japanese", "Korean"],
            "unsupported_languages": [],
            "online_languages": [],
            "privacy_notice": "Qwen3-TTS 配音完全在本机处理。",
        }
        if error:
            response["error"] = error
        return response
    if not settings.TTS_API_URL:
        return {"voices": [], "error": "TTS_API_URL not configured"}
    kokoro_voices = [
        asdict(voice) for voice in await registry.create("kokoro").list_voices()
    ]
    try:
        voices = [
            asdict(voice)
            for voice in await registry.create("speaches").list_voices()
        ] or kokoro_voices
        return {
            "voices": voices + edge_voices,
            "tts_mode": "speaches_edge",
            "unsupported_languages": [],
            "online_languages": ["ko"],
            "privacy_notice": "韩语文本会发送到 Microsoft Edge 在线语音服务。",
        }
    except Exception as e:
        logger.warning(f"Failed to fetch voices from Speaches: {e}")
        return {"voices": kokoro_voices + edge_voices, "error": str(e), "online_languages": ["ko"]}


@router.post("/dubbing/clone-sample")
async def upload_clone_sample(task_id: str = Form(...), file: UploadFile = File(...)):
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    safe_name = Path(file.filename).name
    if not safe_name.lower().endswith((".wav", ".mp3", ".flac", ".ogg", ".m4a")):
        raise HTTPException(status_code=400, detail="Unsupported audio format")

    sample_dir = settings.UPLOAD_DIR / "voice_samples" / task_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    dest = sample_dir / f"clone_{int(time.time())}_{safe_name}"
    with open(dest, "wb") as buf:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            buf.write(chunk)
    return {"path": str(dest), "filename": safe_name}


@router.get("/dubbing/{task_id}")
async def get_dubbing_status(task_id: str):
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "dubbing_status": task.get("dubbing_status"),
        "dubbing_audio_path": task.get("dubbing_audio_path"),
        "dubbing_video_path": task.get("dubbing_video_path"),
        "dubbing_error": task.get("dubbing_error"),
    }
