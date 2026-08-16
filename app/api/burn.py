import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.core.history import history_manager
from app.services.video_burn_service import video_burn_service

logger = logging.getLogger(__name__)
router = APIRouter()


class BurnRequest(BaseModel):
    task_id: str
    show_source: bool = False
    show_target: bool = True
    style: Optional[dict] = None
    target_lang: Optional[str] = None


def _run_burn_task(task_id: str, show_source: bool, show_target: bool, style: Optional[dict], target_lang: Optional[str]):
    try:
        video_burn_service.burn(task_id, show_source, show_target, style or {}, target_lang)
    except Exception:
        logger.exception(f"Background burn task failed for {task_id}")
        # Error state already updated inside burn()


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

    background_tasks.add_task(
        _run_burn_task,
        request.task_id,
        request.show_source,
        request.show_target,
        request.style,
        request.target_lang,
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
