"""Process-local ownership for one integrated pipeline execution.

Context variables propagate through asyncio tasks and asyncio.to_thread. The
shared lock fences cancellation against a worker that is still writing state.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import threading


@dataclass
class TaskRun:
    task_id: str
    run_id: str
    active: bool = True
    started: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def accepts(self, task: dict | None) -> bool:
        return bool(self.active and task and task.get("pipeline_run_id") == self.run_id)


_current_run: ContextVar[TaskRun | None] = ContextVar("pipeline_task_run", default=None)


def current_task_run(task_id: str) -> TaskRun | None:
    run = _current_run.get()
    return run if run and run.task_id == task_id else None


@contextmanager
def task_run_scope(run: TaskRun):
    token = _current_run.set(run)
    try:
        yield
    finally:
        _current_run.reset(token)
