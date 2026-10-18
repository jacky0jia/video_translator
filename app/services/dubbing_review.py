"""Recoverable timing diagnostics use original subtitle indices, not TTS groups."""
from __future__ import annotations

import math


def _indices(values):
    try:
        return sorted({value for value in (values or ()) if type(value) is int and value >= 0})
    except TypeError:
        return []


def dubbing_review_record(error, target_lang: str) -> dict:
    issues = []
    for value in getattr(error, "issues", ()):
        if not isinstance(value, dict):
            continue
        indices = _indices(value.get("segment_indices") or ())
        if not indices:
            continue
        issue = {"segment_indices": indices}
        for key in ("raw_seconds", "fit_seconds", "available_seconds", "excess_seconds", "tempo"):
            number = value.get(key)
            try:
                if type(number) in (int, float) and math.isfinite(number) and number >= 0:
                    issue[key] = number
            except OverflowError:
                continue
        issues.append(issue)
    indices = _indices(index for issue in issues for index in issue["segment_indices"])
    if not indices:
        indices = _indices(getattr(error, "segment_indices", ()))
    return {"status": "required", "target_lang": target_lang,
            "rows": [index + 1 for index in indices], "issues": issues, "needs_recheck": False}
