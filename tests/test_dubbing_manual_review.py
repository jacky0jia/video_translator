import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.core.history import HistoryManager
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.dubbing_review import dubbing_review_record
from app.services.dubbing_service import DubbingCompressionRequired, DubbingService
from app.services.pipeline_service import PipelineService
from app.services.translation_review_service import TranslationReviewService


def issue(indices, excess=2):
    return {"segment_indices": list(indices), "raw_seconds": 8, "fit_seconds": 5,
            "available_seconds": 3, "excess_seconds": excess, "tempo": 1.6}


@pytest.fixture
def manual_case(tmp_path, monkeypatch):
    import app.api.tasks as tasks
    import app.api.translation as translation
    import app.core.history as history_module
    import app.core.events as events
    import app.services.pipeline_service as pipeline
    from app.core.config import settings
    history = HistoryManager(tmp_path / "history.json")
    task_id = history.create_task("fixture.mp4", "local", target_lang="Chinese")
    source, target = tmp_path / "source.json", tmp_path / "target.json"
    for path, language in ((source, "en"), (target, "Chinese")):
        path.write_text(TranscriptionResult(video_source="fixture.mp4", language=language, segments=[
            TranscriptionSegment(start=i, end=i + 1, text=f"Sentence {i}.", confidence=1)
            for i in range(9)
        ]).model_dump_json(), encoding="utf-8")
    history.update_task(task_id, {"transcription_path": str(source), "translation_path": str(target),
                                 "translations": {"Chinese": str(target)}, "compression_attempts": 2})
    for module in (tasks, translation, history_module, pipeline):
        monkeypatch.setattr(module, "history_manager", history)
    monkeypatch.setattr(events, "_task_queues", {})
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tasks, "_ALLOWED_DELETE_DIRS", (tmp_path,))
    review = TranslationReviewService(history)
    token = review.request(task_id, "Chinese")
    review.approve(task_id, "Chinese", token["review_id"])
    return history, task_id, source, target


def test_exhausted_compression_recovers_through_edits_and_remeasurement(manual_case, monkeypatch, tmp_path):
    async def scenario():
        import app.api.tasks as tasks
        history, task_id, _, target = manual_case
        original = target.read_bytes()
        calls = []
        async def dub(*_args):
            calls.append("dub")
            if len(calls) == 1:
                raise DubbingCompressionRequired("too long", [2, 3], issues=[issue([2, 3])])
            if len(calls) == 2:
                raise DubbingCompressionRequired("still long", [3], issues=[issue([3], 0.5)])
            video = tmp_path / "dubbed.mp4"
            video.write_bytes(b"fixture")
            audio = video.with_suffix('.wav'); audio.write_bytes(b"fixture")
            history.update_task(task_id, {"dubbing_video_path": str(video), "dubbing_audio_path": str(audio)})
        translator = AsyncMock()
        service = PipelineService(translate_stage=translator, dub_stage=dub)
        compact = AsyncMock()
        monkeypatch.setattr(service, "_compress_translation", compact)
        monkeypatch.setattr(tasks, "pipeline_service", service)
        await service.start(task_id, "Chinese", "voice")
        paused = history.get_task(task_id)
        assert paused["status"] == paused["pipeline_status"] == "awaiting_dubbing_edit"
        assert paused["dubbing_status"] == "awaiting_edit"
        assert paused["failed_stage"] is None and paused["compression_request"] is None
        assert paused["dubbing_review"]["rows"] == [3, 4]
        assert target.read_bytes() == original
        assert HistoryManager(history.history_file).recover_interrupted_tasks() == 0
        assert not service.is_busy(task_id)
        for index in (2, 3):
            await tasks.edit_segment(task_id, index, tasks.SegmentEdit(target_text="Short."))
            edited = history.get_task(task_id)
            assert edited["dubbing_review"]["needs_recheck"] is True
            assert edited["status"] == "awaiting_review"
            token = edited["translation_reviews"]["Chinese"]["review_id"]
            await tasks.review_translation(task_id, tasks.TranslationReviewDecision(target_lang="Chinese", review_id=token))
            await service.start(task_id, "Chinese", "voice")
            if index == 2:
                state = history.get_task(task_id)
                assert state["status"] == "awaiting_dubbing_edit"
                assert state["dubbing_review"]["rows"] == [4]
                assert state["dubbing_review"]["needs_recheck"] is False
        complete = history.get_task(task_id)
        assert complete["pipeline_status"] == "completed"
        assert complete["dubbing_review"] is None
        assert complete["compression_attempts"] == 2
        assert calls == ["dub", "dub", "dub"]
        translator.assert_not_awaited()
        compact.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("attempts", [0, 1])
def test_earlier_rounds_still_request_explicit_compression_consent(manual_case, monkeypatch, attempts):
    async def scenario():
        history, task_id, _, _ = manual_case
        history.update_task(task_id, {"compression_attempts": attempts})
        async def dub(*_):
            raise DubbingCompressionRequired("too long", [2], issues=[issue([2, 3])])
        service = PipelineService(dub_stage=dub)
        await service.start(task_id, "Chinese", "voice")
        state = history.get_task(task_id)
        assert state["pipeline_status"] == "awaiting_compression"
        assert state["compression_request"]["segment_indices"] == [2]
        assert state["dubbing_review"]["rows"] == [3, 4]
    asyncio.run(scenario())


def test_unmapped_overflow_never_invents_a_subtitle_number(manual_case):
    async def scenario():
        history, task_id, _, _ = manual_case
        async def dub(*_):
            raise DubbingCompressionRequired("unknown mapping", [])
        await PipelineService(dub_stage=dub).start(task_id, "Chinese", "voice")
        state = history.get_task(task_id)
        assert state["pipeline_status"] == "awaiting_dubbing_edit"
        assert state["dubbing_review"]["rows"] == []
    asyncio.run(scenario())


def test_provider_failure_preserves_previous_review_instead_of_claiming_fit(manual_case, monkeypatch):
    async def scenario():
        history, task_id, _, _ = manual_case
        saved = dubbing_review_record(DubbingCompressionRequired("old", [2], issues=[issue([2])]), "Chinese")
        saved["needs_recheck"] = True
        history.update_task(task_id, {"dubbing_review": saved})
        async def fail(*_):
            raise RuntimeError("provider unavailable before measurement")
        await PipelineService(dub_stage=fail).start(task_id, "Chinese", "voice")
        state = history.get_task(task_id)
        assert state["pipeline_status"] == "failed"
        assert state["dubbing_review"] == saved
    asyncio.run(scenario())


def test_utterance_mapping_not_group_number_and_not_limited_to_top_eight():
    groups = [SimpleNamespace(start=i, end=i + 1, segment_indices=(i * 2, i * 2 + 1)) for i in range(10)]
    issues = DubbingService._dubbing_sync_issues(groups, [10] * 10, 1.6, 0.2, 10, 0.6)
    record = dubbing_review_record(DubbingCompressionRequired("long", [0], issues=issues), "Chinese")
    assert record["rows"] == list(range(1, 21))
    assert len(record["issues"]) == 10
    assert record["issues"][0]["raw_seconds"] == 10
    assert record["issues"][0]["fit_seconds"] == 6.25
    assert record["issues"][0]["excess_seconds"] > 0


def test_review_metadata_rejects_nonfinite_measurements_and_invalid_indices():
    payload = {**issue([False, -1, 5, 6, 6, "7"]), "raw_seconds": float("nan"), "fit_seconds": float("inf")}
    record = dubbing_review_record(DubbingCompressionRequired("long", [999], issues=[payload]), "Chinese")
    assert record["rows"] == [6, 7]
    assert "raw_seconds" not in record["issues"][0]
    assert "fit_seconds" not in record["issues"][0]
    json.dumps(record, allow_nan=False)
    assert dubbing_review_record(DubbingCompressionRequired("unknown", None), "Chinese")["rows"] == []


def test_actual_timeline_reports_measurements_then_clears_only_after_fit(tmp_path, monkeypatch):
    import app.services.dubbing_service as module
    state = {"dubbing_review": {"status": "required"}}
    history = SimpleNamespace(update_task=lambda _id, changes: state.update(changes))
    monkeypatch.setattr(module, "history_manager", history)
    service = object.__new__(DubbingService)
    service.ffmpeg_path = "ffmpeg"
    service._probe_duration = lambda _: 10
    groups = [SimpleNamespace(start=0, end=3, segment_indices=(5, 6))]
    with pytest.raises(DubbingCompressionRequired) as error:
        service._build_timeline(groups, [Path("raw.wav")], 3, tmp_path, provider="qwen", task_id="task")
    assert error.value.issues[0]["segment_indices"] == [5, 6]
    assert error.value.issues[0]["raw_seconds"] == 10
    assert state["dubbing_review"] is not None
    service._probe_duration = lambda _: 1
    service._write_silence = lambda *_: None
    service._normalize_natural_audio = lambda _raw, output, _tempo: output
    service._run_ffmpeg = lambda *_: None
    service._build_timeline(groups, [Path("raw.wav")], 3, tmp_path, provider="qwen", task_id="task")
    assert state["dubbing_review"] is None


def test_manual_waiting_snapshot_is_idle_and_survives_refresh(manual_case):
    async def scenario():
        from app.core.events import event_generator, task_event_snapshot
        history, task_id, _, _ = manual_case
        review = dubbing_review_record(DubbingCompressionRequired("long", [5, 6], issues=[issue([5, 6])]), "Chinese")
        history.update_task(task_id, {"status": "awaiting_dubbing_edit", "pipeline_status": "awaiting_dubbing_edit",
                                     "dubbing_status": "awaiting_edit", "dubbing_review": review})
        assert HistoryManager(history.history_file).recover_interrupted_tasks() == 0
        stream = event_generator(task_id, initial_data=task_event_snapshot(history.get_task(task_id)))
        assert json.loads((await anext(stream)).removeprefix("data: "))["dubbing_review"] == review
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 0.5)
    asyncio.run(scenario())
