"""Dubbing API routes. Mirrors the structure of `app/api/burn.py`."""
import logging
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.core.history import history_manager
from app.services.dubbing_service import dubbing_service
from app.services.dubbing_service import _KOKORO_V1_VOICES, _voice_metadata, _is_kokoro_mode
from app.services.edge_tts_service import EdgeTTSService

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
    return language in {"ko", "ko-kr", "korean", "한국어", "韩语", "韓語"}


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
    voice = request.voice
    if settings.TTS_MODE == "edge" and not _is_korean(requested_language):
        raise HTTPException(
            status_code=400,
            detail="当前 Edge provider 仅用于韩语配音；其它语言请选择 Kokoro 或 Speaches。",
        )
    if _is_korean(requested_language):
        if request.clone_sample_path:
            raise HTTPException(status_code=400, detail="Edge 韩语在线配音不支持语音克隆")
        if not voice.lower().startswith("ko-kr-"):
            voice = "ko-KR-SunHiNeural"

    if not (0.25 <= request.speed <= 4.0):
        raise HTTPException(status_code=400, detail="speed must be in [0.25, 4.0]")
    if not _is_korean(requested_language) and _is_kokoro_mode() and not (0.5 <= request.speed <= 2.0):
        raise HTTPException(status_code=400, detail="Kokoro speed must be in [0.5, 2.0]")

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
    edge_voices = []
    edge_error = None
    try:
        edge_voices = await EdgeTTSService(settings.ffmpeg_path).list_voices()
    except Exception as exc:
        edge_error = str(exc)
        logger.warning("Failed to fetch Edge Korean voices: %s", exc)
    if _is_kokoro_mode():
        return {
            "voices": dubbing_service._get_local_voices() + edge_voices,
            "tts_mode": "kokoro_edge",
            "unsupported_languages": [] if edge_voices else ["ko"],
            "online_languages": ["ko"],
            "privacy_notice": "韩语文本会发送到 Microsoft Edge 在线语音服务。",
            "edge_error": edge_error,
        }
    if settings.TTS_MODE == "edge":
        return {
            "voices": edge_voices,
            "tts_mode": "edge",
            "unsupported_languages": [] if edge_voices else ["ko"],
            "online_languages": ["ko"],
            "privacy_notice": "韩语文本会发送到 Microsoft Edge 在线语音服务。",
            "edge_error": edge_error,
        }
    if not settings.TTS_API_URL:
        return {"voices": [], "error": "TTS_API_URL not configured"}
    # Full Kokoro v1.0 voice catalogue with metadata — used as fallback when
    # Speaches' /v1/audio/voices endpoint returns empty (which it does for
    # Kokoro models).  Returning objects with {id, language, gender} lets the
    # frontend filter voices by dubbing language.
    kokoro_voices = [_voice_metadata(v) for v in _KOKORO_V1_VOICES]
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=settings.VERIFY_SSL) as client:
            resp = await client.get(
                f"{dubbing_service._base_url}/v1/audio/voices",
                headers=dubbing_service._headers,
            )
            resp.raise_for_status()
            data = resp.json()
            # Speaches may return {"voices":[...]} or a bare list
            if isinstance(data, dict) and "voices" in data:
                voices = data["voices"]
            elif isinstance(data, list):
                voices = data
            else:
                voices = []
            if not voices:
                # Speaches' /v1/audio/voices doesn't list Kokoro's built-in
                # voices — use the full static catalogue instead.
                voices = kokoro_voices
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
