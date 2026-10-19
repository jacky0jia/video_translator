import asyncio
import copy
import io
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
import pytest

from app.api import burn as api


@pytest.fixture
def saved_task(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.touch()
    saved = {"task_id": "one", "filename": "video.mp4", "source_path": str(video), "status": "completed",
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


@pytest.mark.parametrize("policy", ["balanced", "resolution", "codec", "custom"])
def test_standalone_burn_executes_the_requested_output_strategy(saved_task, monkeypatch, policy):
    saved, _history = saved_task
    render = Mock(return_value="new.mp4")
    monkeypatch.setattr(api.video_burn_service, "burn", render)
    choices = {"policy": policy, "codec": "h264", "resolution": "1080", "bitrate_mbps": 7.5}
    background = BackgroundTasks()
    asyncio.run(api.burn_video(api.BurnRequest(task_id="one", output_options=choices), background))
    assert saved["video_output_options"] == choices
    asyncio.run(background())
    assert render.call_args.kwargs["output_options"] == choices
    assert saved["burn_status"] == "completed"


def test_legacy_burn_request_preserves_legacy_encoding(saved_task, monkeypatch):
    render = Mock(return_value="new.mp4")
    monkeypatch.setattr(api.video_burn_service, "burn", render)
    background = BackgroundTasks()
    asyncio.run(api.burn_video(api.BurnRequest(task_id="one"), background))
    asyncio.run(background())
    assert render.call_args.kwargs.get("output_options") is None


@pytest.mark.parametrize("choices", [{"policy": "unsafe"}, {"codec": "-c:v"},
                                    {"bitrate_mbps": 0}, {"resolution": "99999"}])
def test_invalid_burn_choices_are_rejected_before_reserving_a_job(saved_task, choices):
    saved, history = saved_task
    before = copy.deepcopy(saved)
    with pytest.raises(ValidationError):
        api.BurnRequest(task_id="one", output_options=choices)
    assert saved == before
    history.update_task.assert_not_called()


@pytest.mark.parametrize("hardware_failure", [False, True])
def test_api_choices_reach_real_planner_and_ffmpeg_command(saved_task, monkeypatch, tmp_path, hardware_failure):
    from app.services import video_burn_service as renderer
    from app.services import video_output
    saved, history = saved_task
    monkeypatch.setattr(renderer, "history_manager", history)
    monkeypatch.setattr(renderer, "emit_event", Mock())
    monkeypatch.setattr(renderer.settings, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(renderer.settings, "TEMP_DIR", tmp_path / "temp")
    profile = dict(probed=True, codec="hevc", width=3840, height=2160, duration=1, bitrate=12000000, frame_rate="30/1")
    monkeypatch.setattr(api.video_burn_service, "_probe_video_profile", Mock(side_effect=[profile, {**profile, "width": 1920, "height": 1080}]))
    monkeypatch.setattr(api.video_burn_service, "_generate_ass", Mock(return_value=(tmp_path / "test.ass", 1)))
    # Never probe actual hardware, including a regression to the legacy path.
    monkeypatch.setattr(renderer, "select_encoder", Mock(side_effect=AssertionError("Ignored requested output strategy")))
    def select(_ffmpeg, target, **kwargs):
        hardware = hardware_failure and not kwargs.get("excluded_encoders")
        return dict(codec=target["codec"], encoder="h264_nvenc" if hardware else "libx264",
                    software=not hardware, bitrate=target["bitrate"])
    choose = Mock(side_effect=select)
    monkeypatch.setattr(video_output, "select_encoder", choose)
    processes = [Mock(returncode=1, stderr=io.BytesIO())] if hardware_failure else []
    processes.append(Mock(returncode=0, stderr=io.BytesIO()))
    popen = Mock(side_effect=processes)
    monkeypatch.setattr(renderer.subprocess, "Popen", popen)
    background = BackgroundTasks()
    asyncio.run(api.burn_video(api.BurnRequest(task_id="one", output_options={
        "policy": "custom", "codec": "h264", "resolution": "1080", "bitrate_mbps": 7.5,
    }), background))
    asyncio.run(background())
    assert saved["burn_status"] == "completed"
    for call in popen.call_args_list:
        command = call.args[0]
        assert command[command.index("-vf") + 1].startswith("scale=1920:1080,ass=")
        assert command[command.index("-b:v") + 1] == "7500000"
    command = popen.call_args.args[0]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert saved["video_encoding"]["codec"] == "h264"
    assert saved["video_encoding"]["height"] == 1080
    if hardware_failure:
        assert choose.call_args.kwargs["excluded_encoders"] == ("h264_nvenc",)


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
