import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# In-memory event queues per task_id
_task_queues: Dict[str, asyncio.Queue] = {}


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
    """Get or create an asyncio Queue for a given task."""
    if task_id not in _task_queues:
        _task_queues[task_id] = asyncio.Queue(maxsize=settings.SSE_QUEUE_MAXSIZE)
    return _task_queues[task_id]


def emit_event(task_id: str, data: Dict[str, Any]) -> None:
    """Push an event into the task's SSE queue."""
    # Task state is persisted separately. Do not create an orphan queue when
    # nobody is listening: long dubbing jobs can emit hundreds of events and
    # would otherwise fill a queue that can never be drained.
    queue = _task_queues.get(task_id)
    if queue is None:
        return
    try:
        queue.put_nowait(data)
        logger.debug(f"Emitted event for task {task_id}: {data}")
    except asyncio.QueueFull:
        logger.warning(f"SSE queue full for task {task_id}, dropping event: {data}")


async def event_generator(task_id: str, initial_data: Optional[Dict[str, Any]] = None):
    """
    Async generator that yields SSE formatted events for a task.
    Sends initial_data immediately if provided.
    Closes automatically when terminal status is received.
    """
    queue = _get_queue(task_id)

    # Send initial state if available
    if initial_data:
        yield f"data: {json.dumps(_browser_event(initial_data))}\n\n"

    try:
        while True:
            # Wait for next event with a timeout to keep connection alive
            try:
                data = await asyncio.wait_for(queue.get(), timeout=settings.SSE_KEEPALIVE_TIMEOUT)
            except asyncio.TimeoutError:
                # Send a keep-alive comment to prevent proxy timeouts
                yield ":ping\n\n"
                continue

            yield f"data: {json.dumps(_browser_event(data))}\n\n"

            # Terminal statuses — close the stream
            if data.get("status") in ("transcribed", "completed", "failed", "cancelled"):
                break
    except asyncio.CancelledError:
        # Normal shutdown, don't propagate to ASGI
        pass
    except Exception as e:
        logger.error(f"SSE generator error for task {task_id}: {e}")
    finally:
        # Optional: clean up queue if task is done
        if task_id in _task_queues:
            del _task_queues[task_id]
        logger.info(f"SSE connection closed for task {task_id}")
