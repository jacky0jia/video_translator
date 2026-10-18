import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable

from app.core.config import settings
from app.core.task_run import current_task_run

logger = logging.getLogger(__name__)

TASK_EVENT_FIELDS = (
    "status", "message", "progress_percent", "pipeline_status", "pipeline_stage",
    "failed_stage", "cancelled_stage", "dubbing_status", "burn_status", "gpu_stage",
    "burn_error", "video_codec", "video_encoder", "pipeline_run_id",
    "pipeline_cleanup_pending", "state_revision", "translation_reviews", "translation_review_language", "dubbing_review",
)


def task_event_snapshot(task: dict) -> dict:
    return {key: task.get(key) for key in TASK_EVENT_FIELDS}


def _revision(data: dict):
    value = data.get("state_revision")
    return value if type(value) is int and value >= 0 else None


def _active(data: dict) -> bool:
    return bool(data.get("pipeline_cleanup_pending") or data.get("pipeline_status") == "cancelling"
                or any(data.get(key) == "processing" for key in ("pipeline_status", "dubbing_status", "burn_status")))

# Each SSE connection needs its own queue; a shared queue distributes events
# between listeners instead of broadcasting them.
_task_queues: Dict[str, Dict[asyncio.Queue, asyncio.AbstractEventLoop]] = {}


def _browser_event(data: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize output artifacts as HTTP paths without changing persisted state."""
    def output_url(value):
        if not isinstance(value, str):
            return value
        try:
            relative = Path(value).relative_to(settings.OUTPUT_DIR)
        except ValueError:
            return value
        return f"/static/output/{relative.as_posix()}"

    result = dict(data)
    for key in ("transcription_path", "translation_path", "burn_path",
                "dubbing_audio_path", "dubbing_video_path", "dubbing_raw_video_path"):
        if key in result:
            result[key] = output_url(result[key])
    for key in ("translations", "subtitle_outputs"):
        if isinstance(result.get(key), dict):
            result[key] = {name: output_url(value) for name, value in result[key].items()}
    return result


def _get_queue(task_id: str) -> asyncio.Queue:
    """Register an independent queue for one SSE connection."""
    queue = asyncio.Queue(maxsize=settings.SSE_QUEUE_MAXSIZE)
    _task_queues.setdefault(task_id, {})[queue] = asyncio.get_running_loop()
    return queue


def _deliver(queue: asyncio.Queue, task_id: str, data: Dict[str, Any]) -> None:
    try:
        queue.put_nowait(data)
    except asyncio.QueueFull:
        logger.warning("SSE queue full for task %s; replacing stale event", task_id)
        queue.get_nowait()
        queue.put_nowait(data)


def emit_event(task_id: str, data: Dict[str, Any]) -> None:
    """Push an event into the task's SSE queue."""
    run = current_task_run(task_id)
    if run:
        from app.core.history import history_manager
        with run.lock:
            if not run.accepts(history_manager.get_task(task_id)):
                return
            persisted = history_manager.record_run_event(task_id, {**data, "pipeline_run_id": run.run_id})
            if persisted:
                _emit_event(task_id, {**data, **task_event_snapshot(persisted)})
        return
    _emit_event(task_id, data)


def _emit_event(task_id: str, data: Dict[str, Any]) -> None:
    # Task state is persisted separately. Do not create an orphan queue when
    # nobody is listening: long dubbing jobs can emit hundreds of events and
    # would otherwise fill a queue that can never be drained.
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    for queue, loop in tuple(_task_queues.get(task_id, {}).items()):
        try:
            if loop is current_loop:
                _deliver(queue, task_id, data)
            else:
                loop.call_soon_threadsafe(_deliver, queue, task_id, data)
        except RuntimeError:
            logger.debug("SSE listener loop already closed for task %s", task_id)


async def event_generator(task_id: str, initial_data: Optional[Dict[str, Any]] = None,
                          *, snapshot_loader: Optional[Callable[[], Optional[Dict[str, Any]]]] = None):
    """
    Async generator that yields SSE formatted events for a task.
    Sends initial_data immediately if provided.
    Closes automatically when terminal status is received.
    """
    queue = _get_queue(task_id)
    last_revision = None

    try:
        # Read only after registering the queue: a completion between the HTTP
        # handler's history read and stream startup must not be lost forever.
        snapshot = snapshot_loader() if snapshot_loader else initial_data
        if snapshot_loader and snapshot is None:
            yield 'event: not_found\ndata: {"detail":"Task not found"}\n\n'
            return
        if snapshot:
            last_revision = _revision(snapshot)
            yield f"data: {json.dumps(_browser_event(snapshot))}\n\n"
            if not _active(snapshot) and snapshot.get("status") in {"awaiting_review", "awaiting_dubbing_edit"}:
                return
        while True:
            # Wait for next event with a timeout to keep connection alive
            try:
                data = await asyncio.wait_for(queue.get(), timeout=settings.SSE_KEEPALIVE_TIMEOUT)
            except asyncio.TimeoutError:
                # An event may have been missed or the queue may have filled.
                # Reconcile from persisted state rather than ping forever.
                if snapshot_loader:
                    snapshot = snapshot_loader()
                    if snapshot is None:
                        yield 'event: not_found\ndata: {"detail":"Task not found"}\n\n'
                        return
                    version = _revision(snapshot)
                    if version is not None and last_revision is not None and version < last_revision:
                        continue
                    last_revision = version if version is not None else last_revision
                    yield f"data: {json.dumps(_browser_event(snapshot))}\n\n"
                    if not _active(snapshot) and snapshot.get("status") in ("transcribed", "completed", "failed", "translation_failed", "cancelled", "awaiting_review", "awaiting_dubbing_edit"):
                        return
                else:
                    yield ":ping\n\n"
                continue

            if snapshot_loader and data.get("pipeline_run_id"):
                current = snapshot_loader()
                if current is None:
                    yield 'event: not_found\ndata: {"detail":"Task not found"}\n\n'
                    return
                # A queued terminal event from an older run must not close the
                # stream for the new run, even if it was emitted before restart.
                if data["pipeline_run_id"] != current.get("pipeline_run_id"):
                    continue
                current_version, version = _revision(current), _revision(data)
                if current_version is not None and version is not None and current_version > version:
                    data = current
            version = _revision(data)
            if version is not None and last_revision is not None and version <= last_revision:
                continue
            last_revision = version if version is not None else last_revision
            yield f"data: {json.dumps(_browser_event(data))}\n\n"

            # Terminal statuses — close the stream
            if not _active(data) and data.get("status") in ("transcribed", "completed", "failed", "translation_failed", "cancelled", "awaiting_review", "awaiting_dubbing_edit"):
                break
    except asyncio.CancelledError:
        # Normal shutdown, don't propagate to ASGI
        pass
    except Exception as e:
        logger.error(f"SSE generator error for task {task_id}: {e}")
    finally:
        # Optional: clean up queue if task is done
        subscribers = _task_queues.get(task_id)
        if subscribers is not None:
            subscribers.pop(queue, None)
            if not subscribers:
                _task_queues.pop(task_id, None)
        logger.info(f"SSE connection closed for task {task_id}")
