import copy
import asyncio
import json
import logging
import math
import shutil
from pathlib import Path
from typing import Optional, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from app.services.video_output import VideoOutputOptions, make_output_plan

from app.core.config import settings
from app.core.history import history_manager
from app.core.events import event_generator, emit_event, TASK_EVENT_FIELDS
from app.core.file_security import contained_file
from app.services.pipeline_service import pipeline_service
from app.services.translation_review_service import (
    TranslationReviewService, TranslationReviewConflict, TranslationReviewUnavailable, translation_busy,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_TASK_EVENT_FIELDS = TASK_EVENT_FIELDS


def _task_event_snapshot(task: dict) -> dict:
    """Return the persisted state needed to resume a live progress stream."""
    return {key: task.get(key) for key in _TASK_EVENT_FIELDS}


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


def _delete_subtitle_versions(task, deleted_files=None):
    from app.services.subtitle_timing_versions import subtitle_version_paths
    for value in subtitle_version_paths(task):
        _delete_output_file(value, deleted_files)


def _ensure_tasks_idle(tasks, *, action='deleting'):
    """Preflight before clearing task state or touching any task files."""
    from app.services.dubbing_service import dubbing_service
    active_statuses = {
        'pending', 'uploading', 'downloading', 'extracting_audio', 'transcribing',
        'translating', 'dubbing', 'burning', 'waiting_for_gpu', 'switching_model',
        'pipeline_starting', 'pipeline_transcribing', 'pipeline_translating',
        'pipeline_translated', 'pipeline_dubbing', 'pipeline_rendering', 'pipeline_cancelling',
    }
    for task in tasks:
        if not task:
            continue
        stage_field = {'dubbing': 'dubbing_status', 'burning': 'burn_status'}.get(task.get('status'))
        finished_stage = stage_field and task.get(stage_field) in {'completed', 'failed', 'cancelled'}
        if (
            pipeline_service.is_busy(task['task_id']) or translation_busy(task['task_id'])
            or not dubbing_service.wait_for_stop(task['task_id'], timeout=0)
            or (task.get('status') in active_statuses and not finished_stage)
            or task.get('pipeline_status') in {'processing', 'cancelling'}
            or task.get('pipeline_cleanup_pending')
            or task.get('burn_status') == 'processing'
            or task.get('dubbing_status') == 'processing'
        ):
            raise HTTPException(409, f'Task is running or stopping; cancel it and wait before {action}')


class SegmentEdit(BaseModel):
    source_text: Optional[str] = None
    target_text: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None


class VideoPlanRequest(BaseModel):
    output_options: VideoOutputOptions
    burn_subtitles: bool = False
    end_note_enabled: bool = True


class WorkflowOptions(BaseModel):
    """The last submitted UI request, separate from execution/completion state."""
    model_config = ConfigDict(extra='forbid')
    route: Literal['subtitles', 'dubbing'] = 'subtitles'
    subtitle_format: Literal['srt', 'vtt', 'ass'] = 'srt'
    burn_subtitles: bool = False
    end_note_enabled: bool = True
    subtitle_content: Literal['translated', 'bilingual'] = 'translated'
    voice: str = Field(default='', max_length=256)
    speed: float = Field(default=1, ge=0.25, le=4, allow_inf_nan=False)
    output_options: VideoOutputOptions = Field(default_factory=VideoOutputOptions)
    subtitle_style: dict = Field(default_factory=dict)
    show_source: bool = True
    show_target: bool = True


class TimingApplyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state_revision: int = Field(ge=0, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)


class TimingUndoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state_revision: int = Field(ge=0, strict=True)
    version_id: str = Field(min_length=32, max_length=32)


def _timing_operation(task_id, action, request=None):
    from app.services.subtitle_timing_versions import SubtitleTimingVersions, TimingVersionConflict
    service = SubtitleTimingVersions(history_manager, settings.OUTPUT_DIR, pipeline_service.is_busy)
    try:
        if action == 'analyze':
            return service.analyze(task_id)
        state = (service.apply(task_id, request.state_revision, request.fingerprint) if action == 'apply'
                 else service.undo(task_id, request.state_revision, request.version_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except TimingVersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    changed = state.get('state_revision', 0) != request.state_revision
    if changed:
        emit_event(task_id, _task_event_snapshot(state))
    return {'changed': changed, 'state_revision': state.get('state_revision', 0),
            'active_version': state.get('subtitle_timing_active')}


@router.get('/tasks/{task_id}/subtitle-timing')
async def analyze_subtitle_timing(task_id: str):
    return _timing_operation(task_id, 'analyze')


@router.post('/tasks/{task_id}/subtitle-timing/apply')
async def apply_subtitle_timing(task_id: str, request: TimingApplyRequest):
    return _timing_operation(task_id, 'apply', request)


@router.post('/tasks/{task_id}/subtitle-timing/undo')
async def undo_subtitle_timing(task_id: str, request: TimingUndoRequest):
    return _timing_operation(task_id, 'undo', request)


@router.post('/tasks/{task_id}/workflow-options')
async def save_workflow_options(task_id: str, request: WorkflowOptions):
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, 'Task not found')
    if (pipeline_service.is_busy(task_id) or translation_busy(task_id)
            or task.get('pipeline_cleanup_pending')
            or task.get('pipeline_status') in {'processing', 'cancelling', 'awaiting_compression'}
            or task.get('dubbing_status') == 'processing' or task.get('burn_status') == 'processing'):
        raise HTTPException(409, 'Pipeline already running; workflow options were not changed')
    # This update never claims that a stage ran, approves a review, or changes
    # cached artifacts. Compare-and-set protects a concurrent worker's snapshot.
    saved = history_manager.compare_and_update_task(task_id, task.get('state_revision', 0), {
        'workflow_options': request.model_dump(),
    })
    if not saved:
        raise HTTPException(409, 'Task changed; refresh before continuing')
    return {'workflow_options': saved['workflow_options'], 'state_revision': saved['state_revision']}


@router.post('/tasks/{task_id}/video-plan')
async def preview_video_plan(task_id: str, request: VideoPlanRequest):
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, 'Task not found')
    source = contained_file(settings.UPLOAD_DIR, task.get('upload_path') or task.get('source_path') or '')
    if source is None:
        raise HTTPException(404, 'Source video file not found')
    from app.services.video_burn_service import video_burn_service
    def inspect():
        profile = video_burn_service._probe_video_profile(source)
        plan = make_output_plan(video_burn_service.ffmpeg_path, profile, request.output_options,
                                overlays=request.burn_subtitles or request.end_note_enabled)
        return profile, plan
    try:
        profile, plan = await asyncio.to_thread(inspect)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    history_manager.update_task(task_id, {'source_video_profile': profile})
    return {'source': profile, 'plan': plan}


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

    def current_snapshot():
        current = history_manager.get_task(task_id)
        return _task_event_snapshot(current) if current is not None else None

    return StreamingResponse(
        event_generator(task_id, initial_data=_task_event_snapshot(task), snapshot_loader=current_snapshot),
        media_type="text/event-stream",
    )


@router.post("/tasks/{task_id}/emit")
async def emit_task_event(task_id: str, status: str, message: str = ""):
    """Internal helper to emit an event for a task (for testing or manual triggers)."""
    emit_event(task_id, {"status": status, "message": message})
    return {"status": "emitted"}


class TranslationReviewDecision(BaseModel):
    target_lang: str
    review_id: Optional[str] = None


@router.post("/tasks/{task_id}/translation-review")
async def review_translation(task_id: str, decision: TranslationReviewDecision):
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if (pipeline_service.is_busy(task_id) or translation_busy(task_id)
            or task.get("dubbing_status") == "processing" or task.get("burn_status") == "processing"):
        raise HTTPException(status_code=409, detail="Task is still processing or stopping")
    service = TranslationReviewService(history_manager)
    try:
        if decision.review_id:
            service.approve(task_id, decision.target_lang, decision.review_id)
        else:
            service.pause(task_id, decision.target_lang)
    except TranslationReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TranslationReviewUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = history_manager.get_task(task_id)
    emit_event(task_id, _task_event_snapshot(state))
    return {"approved": service.is_approved(task_id, decision.target_lang),
            "state_revision": state.get("state_revision"),
            "review": (state.get("translation_reviews") or {}).get(decision.target_lang)}


@router.post("/tasks/{task_id}/segments/{index}")
async def edit_segment(task_id: str, index: int, edit: SegmentEdit):
    """
    Edit a subtitle segment's text or timing. Timing is shared by the source
    transcription and every saved translation of the same segment.
    """
    task = history_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if (pipeline_service.is_busy(task_id) or translation_busy(task_id)
            or task.get("dubbing_status") == "processing" or task.get("burn_status") == "processing"):
        raise HTTPException(status_code=409, detail="Pipeline is still processing or stopping")
    updated = []
    timing_edit = edit.start is not None or edit.end is not None
    if timing_edit:
        if (edit.start is None or edit.end is None or
                not math.isfinite(edit.start) or not math.isfinite(edit.end) or
                edit.start < 0 or edit.end <= edit.start):
            raise HTTPException(status_code=400, detail="Start and end must be valid times with end after start")
        values = [task.get("transcription_path"), task.get("translation_path")]
        values.extend((task.get("translations") or {}).values())
        documents = {}
        for value in values:
            if not value:
                continue
            path = Path(value)
            if path in documents:
                continue
            if not path.is_file() or not _is_safe_path(path):
                raise HTTPException(status_code=400, detail="Subtitle file is unavailable")
            with open(path, "r", encoding="utf-8") as source:
                data = json.load(source)
            if not 0 <= index < len(data.get("segments", [])):
                raise HTTPException(status_code=400, detail="Subtitle row is unavailable")
            documents[path] = data
        source_path = Path(task["transcription_path"]) if task.get("transcription_path") else None
        if source_path not in documents:
            raise HTTPException(status_code=400, detail="Source transcription is unavailable")
        for path, data in documents.items():
            data["segments"][index].update(start=edit.start, end=edit.end)
            with open(path, "w", encoding="utf-8") as target:
                json.dump(data, target, ensure_ascii=False, indent=4)
        updated.append("timing")

    # Update source transcription JSON
    if edit.source_text is not None and task.get("transcription_path"):
        path = Path(task["transcription_path"])
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            segments = data.get("segments", [])
            if 0 <= index < len(segments):
                if segments[index].get("text") != edit.source_text:
                    segments[index]["source_text_edited"] = True
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
                data["alignment_review_rows"] = [
                    row for row in data.get("alignment_review_rows", []) if row != index + 1
                ]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                updated.append("target")

    if updated:
        changes = {"subtitle_outputs": {}}
        if edit.target_text is not None:
            alignment_review = dict(task.get("translation_alignment_review") or {})
            target_lang = task.get("target_lang")
            if target_lang in alignment_review:
                review = dict(alignment_review[target_lang])
                review["rows"] = [row for row in review.get("rows", []) if row != index + 1]
                alignment_review[target_lang] = review
                changes["translation_alignment_review"] = alignment_review
        if task.get("dubbing_audio_path") or task.get("dubbing_video_path") or task.get("burn_path"):
            changes.update({"outputs_stale": True, "pipeline_status": "stale"})
            if task.get("status") == "completed":
                changes["status"] = "transcribed"
            if task.get("dubbing_status") == "completed":
                changes["dubbing_status"] = "stale"
            if task.get("burn_status") == "completed":
                changes["burn_status"] = "stale"
        history_manager.update_task(task_id, changes)

    if not updated:
        raise HTTPException(status_code=400, detail="Nothing to update or index out of range")

    if task.get("dubbing_review"):
        history_manager.update_task(task_id, {"dubbing_review": {**task["dubbing_review"], "needs_recheck": True}})
    if task.get("translation_reviews") and task.get("target_lang"):
        TranslationReviewService(history_manager).pause(task_id, task["target_lang"])
        emit_event(task_id, _task_event_snapshot(history_manager.get_task(task_id)))

    return {"status": "success", "updated": updated}


@router.delete("/tasks")
async def delete_all_tasks():
    """Delete all tasks and their associated files."""
    tasks = history_manager.get_all_tasks()
    _ensure_tasks_idle(tasks)
    deleted_count = 0
    for task in tasks:
        task_id = task["task_id"]
        _delete_subtitle_versions(task)
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
    _ensure_tasks_idle([history_manager.get_task(task_id) for task_id in payload.task_ids])
    deleted: list[str] = []
    failed: list[str] = []
    for task_id in payload.task_ids:
        task = history_manager.get_task(task_id)
        if not task:
            failed.append(task_id)
            continue
        _delete_subtitle_versions(task)
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

    _ensure_tasks_idle([task])
    deleted_files: list[str] = []
    _delete_subtitle_versions(task, deleted_files)

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

    _ensure_tasks_idle([task], action='retrying')
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
            "translation_alignment_review": {},
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
