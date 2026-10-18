import asyncio
import copy
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

from fastapi import BackgroundTasks, HTTPException
import pytest

from app.api import burn as api


@pytest.fixture
def saved_task(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.touch()
    saved = {"task_id": "one", "source_path": str(video), "status": "completed",
             "pipeline_status": "completed", "pipeline_stage": "completed", "gpu_stage": "asr",
             "burn_status": "completed", "burn_path": "old.mp4", "burn_error": "old error"}
    history = Mock()
    history.get_task.side_effect = lambda _task: copy.deepcopy(saved)
    history.update_task.side_effect = lambda _task, data: saved.update(data)
    monkeypatch.setattr(api, "history_manager", history)
    monkeypatch.setattr(api, "emit_event", Mock())
    return saved, history


def test_api_reserves_current_burn_before_background_execution(saved_task, monkeypatch):
    saved, _history = saved_task
    burn = Mock()
    monkeypatch.setattr(api.video_burn_service, "burn", burn)
    background = BackgroundTasks()
    result = asyncio.run(api.burn_video(api.BurnRequest(task_id="one"), background))
    assert result["status"] == "started"
    assert saved["burn_status"] == "processing" and saved["status"] == "burning"
    assert saved["pipeline_status"] is None and saved["gpu_stage"] is None
    assert saved["progress_percent"] == 0 and saved["burn_error"] is None
    assert saved["burn_path"] == "old.mp4"  # retain old file, not old completion
    assert len(background.tasks) == 1
    burn.assert_not_called()
    assert asyncio.run(api.get_burn_status("one"))["burn_status"] == "processing"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.burn_video(api.BurnRequest(task_id="one"), BackgroundTasks()))
    assert exc.value.status_code == 409


@pytest.mark.parametrize("active", ["pipeline_status", "dubbing_status", "burn_status"])
def test_conflicting_active_job_is_not_overwritten(saved_task, active):
    saved, history = saved_task
    saved[active] = "processing"
    before = copy.deepcopy(saved)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.burn_video(api.BurnRequest(task_id="one"), BackgroundTasks()))
    assert exc.value.status_code == 409
    assert saved == before
    history.update_task.assert_not_called()


def test_early_worker_failure_cannot_leave_old_completion_or_processing(saved_task, monkeypatch):
    saved, _history = saved_task
    monkeypatch.setattr(api.video_burn_service, "burn", Mock(side_effect=ValueError("Source disappeared")))
    api._run_burn_task("one", False, True, {}, "Chinese", False)
    assert saved["status"] == "failed" and saved["burn_status"] == "failed"
    assert saved["failed_stage"] == "burning" and saved["burn_error"] == "Source disappeared"
    assert saved["burn_path"] == "old.mp4"
    assert api.emit_event.call_args.args[1]["status"] == "failed"


def test_pending_manual_decision_is_not_destroyed_by_a_standalone_render(saved_task):
    saved, history = saved_task
    saved.update(pipeline_status="awaiting_compression", compression_request={"segment_indices": [2]})
    before = copy.deepcopy(saved)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.burn_video(api.BurnRequest(task_id="one"), BackgroundTasks()))
    assert exc.value.status_code == 409
    assert saved == before
    history.update_task.assert_not_called()


def test_successful_standalone_worker_sets_overall_terminal_state(saved_task, monkeypatch):
    saved, _history = saved_task
    monkeypatch.setattr(api.video_burn_service, "burn", Mock(return_value="new.mp4"))
    api._run_burn_task("one", False, True, {}, "Chinese", False)
    assert saved["status"] == "completed" and saved["burn_status"] == "completed"
    assert saved["progress_percent"] == 100


def test_render_snapshots_do_not_use_an_old_pipeline_stage_or_hide_failure():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not available")
    script = r'''
import assert from 'node:assert/strict';
import {deriveTaskProcessingState, stageProgressForEvent} from './src/hooks/taskProcessingState.js';
const legacy = {status:'completed', pipeline_status:'completed', pipeline_stage:'completed', gpu_stage:'asr',
  last_process_route:'subtitles', last_burn_enabled:true, burn_status:'failed', burn_error:'new render failed'};
assert.equal(deriveTaskProcessingState(legacy).status, 'failed');
assert.equal(deriveTaskProcessingState(legacy).currentStage, 'render');
assert.equal(deriveTaskProcessingState(legacy).message, 'new render failed');
assert.equal(deriveTaskProcessingState({...legacy, last_burn_enabled:false}).status, 'completed');
assert.equal(deriveTaskProcessingState({...legacy, last_process_route:'dubbing'}).status, 'completed');
assert.equal(deriveTaskProcessingState({...legacy, status:'translating'}).status, 'processing');
const active = deriveTaskProcessingState({...legacy, burn_status:'processing', progress_percent:55});
assert.equal(active.status, 'processing'); assert.equal(active.currentStage, 'render');
assert.equal(active.progress, 55);
assert.equal(stageProgressForEvent({status:'burning', progress_percent:62}), 62);
assert.equal(stageProgressForEvent({status:'dubbing', progress_percent:73}), 73);
assert.equal(stageProgressForEvent({status:'burning', progress:0, progress_percent:62}), 0);
'''
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--input-type=module", "-e", script], cwd=root / "app/frontend",
                   check=True, capture_output=True, encoding="utf-8", timeout=30)
