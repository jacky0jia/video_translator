import copy
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.history import history_manager
from app.core.events import event_generator, emit_event
from app.core.file_security import contained_file

logger = logging.getLogger(__name__)
router = APIRouter()


def _to_url_path(file_path: Optional[str]) -> Optional[str]:
    """Convert an absolute output file path to a URL-friendly static path."""
    if not file_path:
        return None
    try:
        rel = Path(file_path).relative_to(settings.OUTPUT_DIR)
        return f"/static/output/{rel.as_posix()}"
    except ValueError:
        return file_path


def _video_url(task: dict) -> Optional[str]:
    from urllib.parse import quote
    path = contained_file(settings.UPLOAD_DIR, task.get('upload_path') or '')
    if path and path.parent == settings.UPLOAD_DIR.resolve():
        return '/video/' + quote(path.name, safe='')
    return None


_ALLOWED_DELETE_DIRS = (settings.UPLOAD_DIR, settings.OUTPUT_DIR, settings.TEMP_DIR)


def _is_safe_path(path: Path) -> bool:
    """Ensure the path is under one of the allowed app directories."""
    try:
        resolved = path.resolve()
        return any(resolved == d.resolve() or resolved.is_relative_to(d.resolve()) for d in _ALLOWED_DELETE_DIRS)
    except (ValueError, OSError):
        return False


def _delete_output_file(url_or_path: Optional[str], deleted_files: Optional[list] = None) -> None:
    """Delete an output file referenced by either a /static/output/ URL or an
    absolute path. Handles burn_path / dubbing_audio_path / dubbing_video_path
    which are persisted as URLs.
    """
    if not url_or_path:
        return
    p = Path(url_or_path)
    if url_or_path.startswith("/static/output/"):
        p = settings.OUTPUT_DIR / url_or_path[len("/static/output/"):]
    try:
        if p.exists() and _is_safe_path(p):
            p.unlink()
            if deleted_files is not None:
                deleted_files.append(str(p))
    except Exception as e:
        logger.warning(f"Failed to delete output file {p}: {e}")


class SegmentEdit(BaseModel):
    source_text: Optional[str] = None
    target_text: Optional[str] = None


@router.get("/tasks")
async def list_tasks():
    """List all translation tasks."""
    tasks = history_manager.get_all_tasks()
    for task in tasks:
        task['video_url'] = _video_url(task)
        task["transcription_path"] = _to_url_path(task.get("transcription_path"))
        task["translation_path"] = _to_url_path(task.get("translation_path"))
        task["burn_path"] = _to_url_path(task.get("burn_path"))
        task["dubbing_audio_path"] = _to_url_path(task.get("dubbing_audio_path"))
        task["dubbing_video_path"] = _to_url_path(task.get("dubbing_video_path"))
        task["dubbing_raw_video_path"] = _to_url_path(task.get("dubbing_raw_video_path"))
        task["subtitle_outputs"] = {
            key: _to_url_path(value)
            for key, value in (task.get("subtitle_outputs") or {}).items()
        }
        if task.get("translations"):
            task["translations"] = {
                k: _to_url_path(v) for k, v in task["translations"].items()
            }
    return {"tasks": tasks}


@router.get("/tasks/{task_id}")
async def get_task_details(task_id: str):
    """Get details for a specific task."""
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task = copy.deepcopy(task)
    task['video_url'] = _video_url(task)
    task["transcription_path"] = _to_url_path(task.get("transcription_path"))
    task["translation_path"] = _to_url_path(task.get("translation_path"))
    task["burn_path"] = _to_url_path(task.get("burn_path"))
    task["dubbing_audio_path"] = _to_url_path(task.get("dubbing_audio_path"))
    task["dubbing_video_path"] = _to_url_path(task.get("dubbing_video_path"))
    task["dubbing_raw_video_path"] = _to_url_path(task.get("dubbing_raw_video_path"))
    task["subtitle_outputs"] = {
        key: _to_url_path(value)
        for key, value in (task.get("subtitle_outputs") or {}).items()
    }
    if task.get("translations"):
        task["translations"] = {
            k: _to_url_path(v) for k, v in task["translations"].items()
        }
    return {"task": task}


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    """
    Server-Sent Events stream for real-time task status updates.
    """
    task = history_manager.get_task(task_id)
    if not task:
        async def not_found_stream():
            yield "event: not_found\ndata: {\"detail\":\"Task not found\"}\n\n"
        return StreamingResponse(
            not_found_stream(),
            media_type="text/event-stream",
        )

    return StreamingResponse(
        event_generator(task_id, initial_data={"status": task.get("status", "unknown")}),
        media_type="text/event-stream",
    )


@router.post("/tasks/{task_id}/emit")
async def emit_task_event(task_id: str, status: str, message: str = ""):
    """Internal helper to emit an event for a task (for testing or manual triggers)."""
    emit_event(task_id, {"status": status, "message": message})
    return {"status": "emitted"}


@router.post("/tasks/{task_id}/segments/{index}")
async def edit_segment(task_id: str, index: int, edit: SegmentEdit):
    """
    Edit a specific subtitle segment's source and/or target text.
    Timestamps are preserved; only the text field is modified.
    """
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    updated = []

    # Update source transcription JSON
    if edit.source_text is not None and task.get("transcription_path"):
        path = Path(task["transcription_path"])
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            segments = data.get("segments", [])
            if 0 <= index < len(segments):
                segments[index]["text"] = edit.source_text
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                updated.append("source")

    # Update translated transcription JSON
    if edit.target_text is not None and task.get("translation_path"):
        path = Path(task["translation_path"])
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            segments = data.get("segments", [])
            if 0 <= index < len(segments):
                segments[index]["text"] = edit.target_text
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                updated.append("target")

    if not updated:
        raise HTTPException(status_code=400, detail="Nothing to update or index out of range")

    return {"status": "success", "updated": updated}


@router.delete("/tasks")
async def delete_all_tasks():
    """Delete all tasks and their associated files."""
    tasks = history_manager.get_all_tasks()
    deleted_count = 0
    for task in tasks:
        task_id = task["task_id"]
        # Delete uploaded video
        upload_path = task.get("upload_path")
        if upload_path:
            p = Path(upload_path)
            if p.exists() and _is_safe_path(p):
                try:
                    p.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete upload {p}: {e}")
        # Delete transcription file
        transcription_path = task.get("transcription_path")
        if transcription_path:
            p = Path(transcription_path)
            if p.exists() and _is_safe_path(p):
                try:
                    p.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete transcription {p}: {e}")
        # Delete translation file
        translation_path = task.get("translation_path")
        if translation_path:
            p = Path(translation_path)
            if p.exists() and _is_safe_path(p):
                try:
                    p.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete translation {p}: {e}")
        # Delete extra translations
        translations = task.get("translations", {})
        for path in translations.values():
            if path:
                p = Path(path)
                if p.exists() and _is_safe_path(p):
                    try:
                        p.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to delete translation file {p}: {e}")
        # Delete burn / dubbing output files
        _delete_output_file(task.get("burn_path"))
        _delete_output_file(task.get("dubbing_audio_path"))
        _delete_output_file(task.get("dubbing_video_path"))
        if task.get("dubbing_raw_video_path") != task.get("dubbing_video_path"):
            _delete_output_file(task.get("dubbing_raw_video_path"))
        for output_path in (task.get("subtitle_outputs") or {}).values():
            _delete_output_file(output_path)
        # Delete temp dir
        for temp_dir in (settings.TEMP_DIR / task_id, settings.TEMP_DIR / f"dub_{task_id}"):
            if temp_dir.exists() and temp_dir.is_dir() and _is_safe_path(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to delete temp dir {temp_dir}: {e}")
        deleted_count += 1

    # Clear history
    history_manager._write_history([])
    return {"status": "deleted_all", "deleted_count": deleted_count}


from pydantic import BaseModel

class BatchDeleteRequest(BaseModel):
    task_ids: list[str]


@router.post("/tasks/batch-delete")
async def batch_delete_tasks(payload: BatchDeleteRequest):
    """Delete multiple tasks and their associated files."""
    deleted: list[str] = []
    failed: list[str] = []
    for task_id in payload.task_ids:
        task = history_manager.get_task(task_id)
        if not task:
            failed.append(task_id)
            continue
        # Delete uploaded video
        upload_path = task.get("upload_path")
        if upload_path:
            p = Path(upload_path)
            if p.exists() and _is_safe_path(p):
                try:
                    p.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete upload {p}: {e}")
        # Delete transcription file
        transcription_path = task.get("transcription_path")
        if transcription_path:
            p = Path(transcription_path)
            if p.exists() and _is_safe_path(p):
                try:
                    p.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete transcription {p}: {e}")
        # Delete translation file
        translation_path = task.get("translation_path")
        if translation_path:
            p = Path(translation_path)
            if p.exists() and _is_safe_path(p):
                try:
                    p.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete translation {p}: {e}")
        # Delete extra translations
        translations = task.get("translations", {})
        for path in translations.values():
            if path:
                p = Path(path)
                if p.exists() and _is_safe_path(p):
                    try:
                        p.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to delete translation file {p}: {e}")
        # Delete burn / dubbing output files
        _delete_output_file(task.get("burn_path"))
        _delete_output_file(task.get("dubbing_audio_path"))
        _delete_output_file(task.get("dubbing_video_path"))
        if task.get("dubbing_raw_video_path") != task.get("dubbing_video_path"):
            _delete_output_file(task.get("dubbing_raw_video_path"))
        for output_path in (task.get("subtitle_outputs") or {}).values():
            _delete_output_file(output_path)
        # Delete temp dir
        for temp_dir in (settings.TEMP_DIR / task_id, settings.TEMP_DIR / f"dub_{task_id}"):
            if temp_dir.exists() and temp_dir.is_dir() and _is_safe_path(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to delete temp dir {temp_dir}: {e}")
        history_manager.delete_task(task_id)
        deleted.append(task_id)
    return {"status": "batch_deleted", "deleted": deleted, "failed": failed}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """
    Delete a task and its associated files (upload video, temp chunks, output files).
    The history record itself is also removed.
    """
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    deleted_files: list[str] = []

    # Delete uploaded video
    upload_path = task.get("upload_path")
    if upload_path:
        p = Path(upload_path)
        if p.exists() and _is_safe_path(p):
            try:
                p.unlink()
                deleted_files.append(str(p))
            except Exception as e:
                logger.warning(f"Failed to delete upload {p}: {e}")

    # Delete transcription file
    transcription_path = task.get("transcription_path")
    if transcription_path:
        p = Path(transcription_path)
        if p.exists() and _is_safe_path(p):
            try:
                p.unlink()
                deleted_files.append(str(p))
            except Exception as e:
                logger.warning(f"Failed to delete transcription {p}: {e}")

    # Delete translation file
    translation_path = task.get("translation_path")
    if translation_path:
        p = Path(translation_path)
        if p.exists() and _is_safe_path(p):
            try:
                p.unlink()
                deleted_files.append(str(p))
            except Exception as e:
                logger.warning(f"Failed to delete translation {p}: {e}")

    # Delete any extra translation outputs
    translations = task.get("translations", {})
    for path in translations.values():
        if path:
            p = Path(path)
            if p.exists() and _is_safe_path(p):
                try:
                    p.unlink()
                    deleted_files.append(str(p))
                except Exception as e:
                    logger.warning(f"Failed to delete translation file {p}: {e}")

    # Delete burn / dubbing output files
    _delete_output_file(task.get("burn_path"), deleted_files)
    _delete_output_file(task.get("dubbing_audio_path"), deleted_files)
    _delete_output_file(task.get("dubbing_video_path"), deleted_files)
    if task.get("dubbing_raw_video_path") != task.get("dubbing_video_path"):
        _delete_output_file(task.get("dubbing_raw_video_path"), deleted_files)
    for output_path in (task.get("subtitle_outputs") or {}).values():
        _delete_output_file(output_path, deleted_files)

    # Delete task-specific temp directory if it exists
    for temp_dir in (settings.TEMP_DIR / task_id, settings.TEMP_DIR / f"dub_{task_id}"):
        if temp_dir.exists() and temp_dir.is_dir() and _is_safe_path(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                deleted_files.append(str(temp_dir))
            except Exception as e:
                logger.warning(f"Failed to delete temp dir {temp_dir}: {e}")

    # Remove from history
    history_manager.delete_task(task_id)

    return {"status": "deleted", "task_id": task_id, "deleted_files": deleted_files}


@router.post("/tasks/{task_id}/retry")
async def retry_task(task_id: str, background_tasks: BackgroundTasks):
    """Retry a transcription task using the original source file."""
    from app.api.transcribe import run_transcription_task

    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="taskNotFound")

    source_path = task.get("source_path") or task.get("upload_path")
    if not source_path:
        raise HTTPException(status_code=400, detail="retryNoSourceFile")

    if not Path(source_path).exists():
        raise HTTPException(status_code=400, detail="retrySourceFileMissing")

    # Reset task status
    history_manager.update_task(
        task_id,
        {
            "status": "pending",
            "message": None,
            "transcription_path": None,
            "translation_path": None,
            "translations": {},
            "translation_profiles": {},
            "subtitle_outputs": {},
            "pipeline_translation_targets": [],
            "pipeline_status": None,
            "pipeline_stage": None,
            "failed_stage": None,
            "cancelled_stage": None,
            "burn_status": None,
            "burn_path": None,
            "burn_error": None,
            "dubbing_status": None,
            "dubbing_audio_path": None,
            "dubbing_raw_video_path": None,
            "dubbing_video_path": None,
            "dubbing_error": None,
            "dubbing_target_lang": None,
            "dubbing_voice": None,
            "dubbing_speed": None,
        },
    )
    emit_event(task_id, {"status": "pending", "message": "正在重新转录...", "progress_percent": 0})

    # Restart background task
    background_tasks.add_task(
        run_transcription_task,
        task_id,
        source_path,
        task.get("filename", "unknown_video"),
        task.get("language"),
    )

    return {"status": "success", "task_id": task_id, "message": "任务重试已启动"}
