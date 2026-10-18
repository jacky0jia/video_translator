"""Persistent, content-bound translation review decisions.

This module does not start or resume workflows. Callers must request a new
review after every generated translation (even identical output), and check
approval again immediately before consuming subtitles. File editing and task
updates must share the caller's workflow exclusion: a history revision alone
cannot make separate subtitle files transactional.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from functools import wraps
from inspect import signature
from pathlib import Path
from uuid import uuid4

_translation_jobs: set[str] = set()
_jobs_lock = threading.Lock()


def translation_busy(task_id: str) -> bool:
    with _jobs_lock:
        return task_id in _translation_jobs


def exclusive_translation(function):
    """Reserve a task across the async provider call; always release on cancel."""
    parameters = signature(function)

    @wraps(function)
    async def guarded(*args, **kwargs):
        from fastapi import HTTPException
        task_id = parameters.bind_partial(*args, **kwargs).arguments.get("task_id")
        if not task_id:
            return await function(*args, **kwargs)
        with _jobs_lock:
            if task_id in _translation_jobs:
                raise HTTPException(status_code=409, detail="Translation is still processing")
            _translation_jobs.add(task_id)
        try:
            return await function(*args, **kwargs)
        finally:
            with _jobs_lock:
                _translation_jobs.discard(task_id)
    return guarded


class TranslationReviewConflict(RuntimeError):
    """The task or subtitle version changed while a decision was being made."""


class TranslationReviewUnavailable(RuntimeError):
    """Source or translated subtitles are unavailable or invalid."""


def _revision(task: dict) -> int:
    value = task.get("state_revision")
    return value if type(value) is int and value >= 0 else 0


def _subtitle_document(value) -> dict:
    if not isinstance(value, str) or not value:
        raise TranslationReviewUnavailable("Subtitle file is unavailable")
    try:
        document = json.loads(Path(value).read_text(encoding="utf-8-sig"))
        segments = document["segments"]
        if not isinstance(segments, list) or not segments:
            raise ValueError("Invalid subtitle rows")
        for row in segments:
            if not isinstance(row, dict) or not isinstance(row.get("text"), str):
                raise ValueError("Invalid subtitle text")
            start, end = row.get("start"), row.get("end")
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end)
                    or start < 0 or end <= start):
                raise ValueError("Invalid subtitle timing")
        # Reject non-finite values anywhere, without changing the source file.
        json.dumps(document, allow_nan=False)
        return document
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise TranslationReviewUnavailable("Subtitle file is unavailable or invalid") from exc


def translation_fingerprint(task: dict, target_lang: str) -> str:
    """Hash the selected language and both documents; formatting is irrelevant."""
    if not isinstance(target_lang, str) or not target_lang.strip():
        raise TranslationReviewUnavailable("Target language is unavailable")
    path = (task.get("translations") or {}).get(target_lang)
    if not path and task.get("target_lang") == target_lang:
        path = task.get("translation_path")
    payload = {
        "task_id": task["task_id"], "target_lang": target_lang,
        "source": _subtitle_document(task.get("transcription_path")),
        "translation": _subtitle_document(path),
    }
    if len(payload["source"]["segments"]) != len(payload["translation"]["segments"]):
        raise TranslationReviewUnavailable("Source and translation row counts differ")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TranslationReviewService:
    def __init__(self, history):
        self.history = history

    def _task(self, task_id: str) -> dict:
        task = self.history.get_task(task_id)
        if task is None:
            raise TranslationReviewUnavailable("Task not found")
        return task

    def _save(self, task: dict, target_lang: str, review: dict, updates=None) -> dict:
        reviews = dict(task.get("translation_reviews") or {})
        reviews[target_lang] = review
        state = self.history.compare_and_update_task(
            task["task_id"], _revision(task), {**(updates or {}), "translation_reviews": reviews},
        )
        if not state:
            raise TranslationReviewConflict("Task changed; refresh before reviewing subtitles")
        return dict(review)

    def pause(self, task_id: str, target_lang: str) -> bool:
        """Persist an idle review boundary, without regenerating edited text."""
        if self.is_approved(task_id, target_lang):
            return False
        task = self._task(task_id)
        fingerprint = translation_fingerprint(task, target_lang)
        review = (task.get("translation_reviews") or {}).get(target_lang) or {}
        if review.get("status") != "pending" or review.get("fingerprint") != fingerprint:
            review = {"review_id": uuid4().hex, "fingerprint": fingerprint, "status": "pending"}
        updates = {
            "status": "awaiting_review", "pipeline_status": "awaiting_review",
            "pipeline_stage": None, "gpu_stage": None, "progress_percent": 0,
            "translation_review_language": target_lang, "target_lang": target_lang,
            "translation_path": (task.get("translations") or {}).get(target_lang) or task.get("translation_path"),
            "message": "翻译已完成，请检查字幕后点击继续。",
            "failed_stage": None, "cancelled_stage": None,
            "subtitle_outputs": {}, "outputs_stale": bool(
                task.get("dubbing_audio_path") or task.get("dubbing_video_path")
                or task.get("dubbing_raw_video_path") or task.get("burn_path")
            ),
        }
        for stage in ("dubbing", "burn"):
            if task.get(f"{stage}_status") == "completed":
                updates[f"{stage}_status"] = "stale"
        self._save(task, target_lang, review, updates)
        return True

    def request(self, task_id: str, target_lang: str) -> dict:
        """Always create a fresh decision token, including for identical output."""
        task = self._task(task_id)
        return self._save(task, target_lang, {
            "review_id": uuid4().hex,
            "fingerprint": translation_fingerprint(task, target_lang),
            "status": "pending",
        })

    def approve(self, task_id: str, target_lang: str, review_id: str) -> dict:
        task = self._task(task_id)
        review = (task.get("translation_reviews") or {}).get(target_lang) or {}
        if (not review_id or review.get("review_id") != review_id
                or review.get("status") not in {"pending", "approved"}):
            raise TranslationReviewConflict("This review decision is no longer current")
        if review.get("fingerprint") != translation_fingerprint(task, target_lang):
            raise TranslationReviewConflict("Subtitles changed; review the edited version first")
        if review["status"] == "approved":
            return dict(review)  # A repeated click is idempotent, not a new decision.
        updates = {}
        if task.get("pipeline_status") == "awaiting_review":
            updates = {"status": "transcribed", "pipeline_status": None,
                       "pipeline_stage": None, "gpu_stage": None, "message": "字幕已确认"}
        return self._save(task, target_lang, {**review, "status": "approved"}, updates)

    def is_approved(self, task_id: str, target_lang: str) -> bool:
        try:
            task = self._task(task_id)
            review = (task.get("translation_reviews") or {}).get(target_lang) or {}
            return (review.get("status") == "approved"
                    and bool(review.get("review_id"))
                    and review.get("fingerprint") == translation_fingerprint(task, target_lang))
        except TranslationReviewUnavailable:
            return False
