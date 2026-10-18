import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core.history import history_manager
from app.core.events import emit_event
from app.services.video_burn_service import video_burn_service

logger = logging.getLogger(__name__)
router = APIRouter()


class BurnRequest(BaseModel):
    task_id: str
    show_source: bool = False
    show_target: bool = True
    style: Optional[dict] = None
    target_lang: Optional[str] = None
    end_note_enabled: bool = True


def _run_burn_task(task_id: str, show_source: bool, show_target: bool, style: Optional[dict], target_lang: Optional[str], end_note_enabled: bool):
    try:
        video_burn_service.burn(task_id, show_source, show_target, style or {}, target_lang, end_note_enabled=end_note_enabled)
        data = {"status": "completed", "burn_status": "completed", "progress_percent": 100,
                "message": "completed", "failed_stage": None}
        history_manager.update_task(task_id, data)
        emit_event(task_id, data)
    except Exception as exc:
        logger.exception(f"Background burn task failed for {task_id}")
        # Validation can fail before burn() enters its own protected block.
        data = {"status": "failed", "burn_status": "failed", "burn_error": str(exc),
                "message": str(exc), "failed_stage": "burning"}
        history_manager.update_task(task_id, data)
        emit_event(task_id, data)


@router.post("/burn")
async def burn_video(request: BurnRequest, background_tasks: BackgroundTasks):
    task = history_manager.get_task(request.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    video_path = task.get("upload_path") or task.get("source_path")
    if not video_path:
        raise HTTPException(status_code=400, detail="Source video path not found")

    from pathlib import Path
    if not Path(video_path).exists():
        raise HTTPException(status_code=400, detail="Source video file not found")

    if task.get("burn_status") == "processing":
        raise HTTPException(status_code=409, detail="Burn already in progress")
    if task.get("pipeline_status") == "awaiting_compression":
        raise HTTPException(status_code=409, detail="Resolve the pending compression decision before rendering")
    if task.get("pipeline_status") == "processing" or task.get("dubbing_status") == "processing":
        raise HTTPException(status_code=409, detail="Processing already in progress")

    # Reserve the standalone operation before BackgroundTasks can be delayed.
    # Leave old output paths available, but never report them as this run's result.
    data = {"status": "burning", "burn_status": "processing", "burn_error": None,
            "message": "正在烧录字幕...", "progress_percent": 0,
            "pipeline_status": None, "pipeline_stage": None, "gpu_stage": None,
            "failed_stage": None, "cancelled_stage": None,
            "last_process_route": "subtitles", "last_burn_enabled": True,
            "last_end_note_enabled": request.end_note_enabled}
    history_manager.update_task(request.task_id, data)
    emit_event(request.task_id, data)

    background_tasks.add_task(
        _run_burn_task,
        request.task_id,
        request.show_source,
        request.show_target,
        request.style,
        request.target_lang,
        request.end_note_enabled,
    )

    return {"status": "started", "task_id": request.task_id}


@router.get("/burn/{task_id}")
async def get_burn_status(task_id: str):
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "burn_status": task.get("burn_status"),
        "burn_path": task.get("burn_path"),
        "burn_error": task.get("burn_error"),
    }
