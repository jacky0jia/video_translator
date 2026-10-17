import asyncio
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from app.core.schemas import TranscriptionResult, TranscriptionSegment


def test_frontend_transcription_review_rules():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not available")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--test", "tests/transcription-review.test.mjs"], cwd=root,
                   check=True, capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_legacy_schema_defaults_to_unedited_and_preserves_manual_provenance():
    row = TranscriptionSegment(start=0, end=8, text="Original", confidence=-1.1)
    assert row.source_text_edited is False
    row.source_text_edited = True
    saved = TranscriptionResult(video_source="source.wav", language="en", segments=[row])
    loaded = TranscriptionResult.model_validate_json(saved.model_dump_json())
    assert loaded.segments[0].source_text_edited is True
    assert loaded.segments[0].confidence == -1.1


@pytest.mark.parametrize("kind", ["source", "cleared", "target", "timing", "unchanged"])
def test_edit_api_only_marks_changed_source_text_and_preserves_original_score(tmp_path, monkeypatch, kind):
    from app.api import tasks as api
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    data = {"video_source": "source.wav", "language": "en", "segments": [
        {"start": 0, "end": 8, "text": "Original", "confidence": -1.1},
    ]}
    original = json.dumps(data)
    source.write_text(original, encoding="utf-8")
    target.write_text(original, encoding="utf-8")
    history = Mock()
    history.get_task.return_value = {"transcription_path": str(source), "translation_path": str(target)}
    monkeypatch.setattr(api, "history_manager", history)
    monkeypatch.setattr(api, "_is_safe_path", lambda _path: True)
    options = {"source": {"source_text": "Corrected"}, "cleared": {"source_text": ""},
               "target": {"target_text": "译文"}, "timing": {"start": 1, "end": 7},
               "unchanged": {"source_text": "Original"}}[kind]
    asyncio.run(api.edit_segment("test", 0, api.SegmentEdit(**options)))
    loaded = TranscriptionResult.model_validate_json(source.read_text(encoding="utf-8"))
    assert loaded.segments[0].source_text_edited is (kind in ("source", "cleared"))
    assert loaded.segments[0].confidence == -1.1
    assert json.loads(target.read_text(encoding="utf-8"))["segments"][0].get("source_text_edited", False) is False
