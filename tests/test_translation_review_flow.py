"""Execute real history/review/API boundaries with fake model and media stages."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.core.history import HistoryManager
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.pipeline_service import PipelineService
from app.services.translation_review_service import TranslationReviewService, translation_busy


@pytest.fixture
def flow(tmp_path, monkeypatch):
    import app.api.tasks as tasks
    import app.api.translation as translation
    import app.api.pipeline as pipeline_api
    import app.api.burn as burn
    import app.api.dubbing as dubbing
    import app.core.history as history_module
    import app.services.pipeline_service as pipeline
    import app.core.events as events
    from app.core.config import settings

    history = HistoryManager(tmp_path / "history.json")
    task_id = history.create_task("fixture.mp4", "local", target_lang="Chinese")
    source, target, video = tmp_path / "source.json", tmp_path / "target.json", tmp_path / "fixture.mp4"
    video.write_bytes(b"fixture")
    for path, text, language in ((source, "Hello", "en"), (target, "你好", "Chinese")):
        path.write_text(TranscriptionResult(video_source="fixture.mp4", language=language, segments=[
            TranscriptionSegment(start=0, end=2, text=text, confidence=1),
        ]).model_dump_json(), encoding="utf-8")
    history.update_task(task_id, {"status": "transcribed", "transcription_path": str(source),
                                 "translation_path": str(target), "translations": {"Chinese": str(target)},
                                 "source_path": str(video), "upload_path": str(video)})
    for module in (tasks, translation, pipeline_api, burn, dubbing, pipeline, history_module):
        monkeypatch.setattr(module, "history_manager", history)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tasks, "_ALLOWED_DELETE_DIRS", (tmp_path,))
    monkeypatch.setattr(events, "_task_queues", {})
    return history, task_id, source, target


def test_pipeline_waits_before_export_and_tts_then_resumes_edited_text(flow, monkeypatch, tmp_path):
    async def scenario():
        import app.api.tasks as tasks
        import app.services.pipeline_service as module
        history, task_id, _, target = flow
        calls = []

        async def export(*_args):
            calls.append("export")

        async def dub(*_args):
            calls.append(json.loads(target.read_text(encoding="utf-8"))["segments"][0]["text"])
            video = tmp_path / "dubbed.mp4"
            video.write_bytes(b"fixture")
            audio = video.with_suffix('.wav'); audio.write_bytes(b"fixture")
            history.update_task(task_id, {"dubbing_video_path": str(video), "dubbing_audio_path": str(audio)})

        translator = AsyncMock()
        service = PipelineService(translate_stage=translator, export_stage=export, dub_stage=dub)
        monkeypatch.setattr(tasks, "pipeline_service", service)
        await service.start(task_id, "Chinese", "voice")
        paused = history.get_task(task_id)
        old_token = paused["translation_reviews"]["Chinese"]["review_id"]
        assert paused["pipeline_status"] == "awaiting_review"
        assert not service.is_busy(task_id)
        assert calls == []
        assert HistoryManager(history.history_file).recover_interrupted_tasks() == 0
        await tasks.edit_segment(task_id, 0, tasks.SegmentEdit(target_text="人工修改"))
        current = history.get_task(task_id)["translation_reviews"]["Chinese"]
        assert current["review_id"] != old_token
        with pytest.raises(HTTPException) as error:
            await tasks.review_translation(task_id, tasks.TranslationReviewDecision(target_lang="Chinese", review_id=old_token))
        assert error.value.status_code == 409
        await tasks.review_translation(task_id, tasks.TranslationReviewDecision(target_lang="Chinese", review_id=current["review_id"]))
        await service.start(task_id, "Chinese", "voice")
        assert calls == ["export", "人工修改"]
        assert history.get_task(task_id)["pipeline_status"] == "completed"
        translator.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize("pipeline", [False, True])
def test_generated_translation_always_gets_fresh_review_even_if_identical(flow, monkeypatch, pipeline):
    async def scenario():
        import app.api.translation as api
        history, task_id, source, _ = flow
        reviewer = TranslationReviewService(history)
        old = reviewer.request(task_id, "Chinese")
        reviewer.approve(task_id, "Chinese", old["review_id"])
        result = TranscriptionResult(video_source="fixture.mp4", language="Chinese", segments=[
            TranscriptionSegment(start=0, end=2, text="你好", confidence=1),
        ])
        monkeypatch.setattr(api.translation_service, "translate_full_result", AsyncMock(return_value=result))
        await api.translate_transcription(str(source), "Chinese", task_id, pipeline=pipeline)
        first = history.get_task(task_id)["translation_reviews"]["Chinese"]
        assert first["review_id"] != old["review_id"]
        assert not reviewer.is_approved(task_id, "Chinese")
        reviewer.approve(task_id, "Chinese", first["review_id"])
        await api.translate_transcription(str(source), "Chinese", task_id, pipeline=pipeline)
        assert not reviewer.is_approved(task_id, "Chinese")
        # A query flag alone is not an owned pipeline execution. Direct API
        # callers must pause too instead of leaving a phantom active stage.
        assert history.get_task(task_id)["pipeline_status"] == "awaiting_review"
    asyncio.run(scenario())


def test_subtitle_export_and_direct_media_endpoints_cannot_bypass_review(flow, monkeypatch):
    async def scenario():
        import app.api.translation as translation
        import app.api.tasks as tasks
        import app.api.burn as burn
        import app.api.dubbing as dubbing
        history, task_id, _, target = flow
        reviewer = TranslationReviewService(history)
        reviewer.pause(task_id, "Chinese")
        for operation in (
            lambda: translation.export_subtitles(str(target), "srt", task_id, "translated", download=False),
            lambda: burn.burn_video(burn.BurnRequest(task_id=task_id), BackgroundTasks()),
            lambda: dubbing.start_dubbing(dubbing.DubbingRequest(task_id=task_id, voice="voice"), BackgroundTasks()),
        ):
            with pytest.raises(HTTPException) as error:
                await operation()
            assert error.value.status_code == 409
        token = history.get_task(task_id)["translation_reviews"]["Chinese"]["review_id"]
        await tasks.review_translation(task_id, tasks.TranslationReviewDecision(target_lang="Chinese", review_id=token))
        exported = await translation.export_subtitles(
            str(target), "srt", task_id, "translated", extra_json_paths=None,
            source_color=None, target_color=None, font_size=None, offset_y=None,
            bold=None, font_family=None, download=False, process_route="subtitles", burn_enabled=False,
        )
        assert exported["status"] == "success"
        assert history.get_task(task_id)["status"] == "completed"
        await tasks.edit_segment(task_id, 0, tasks.SegmentEdit(source_text="Source changed"))
        assert not reviewer.is_approved(task_id, "Chinese")
        with pytest.raises(HTTPException):
            await translation.export_subtitles(str(target), "srt", task_id, "translated", download=False)
    asyncio.run(scenario())


def test_pending_preparation_does_not_approve_and_is_idempotent(flow):
    async def scenario():
        import app.api.tasks as tasks
        history, task_id, _, _ = flow
        decision = tasks.TranslationReviewDecision(target_lang="Chinese")
        first = await tasks.review_translation(task_id, decision)
        second = await tasks.review_translation(task_id, decision)
        assert first["approved"] is second["approved"] is False
        assert first["review"] == second["review"]
        assert history.get_task(task_id)["status"] == "awaiting_review"
        assert history.get_task(task_id)["outputs_stale"] is False
    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["dubbing_audio_path", "burn_path"])
def test_review_marks_existing_media_stale_but_preserves_its_path(flow, field):
    history, task_id, _, _ = flow
    history.update_task(task_id, {field: "existing-media", "dubbing_status": "completed", "burn_status": "completed"})
    TranslationReviewService(history).pause(task_id, "Chinese")
    task = history.get_task(task_id)
    assert task["outputs_stale"] is True
    assert task[field] == "existing-media"
    assert task["dubbing_status"] == task["burn_status"] == "stale"


def test_fresh_asr_and_real_translation_api_stop_at_review_then_continue_once(flow, monkeypatch):
    async def scenario():
        import app.api.translation as api
        import app.api.tasks as tasks
        history, task_id, source, _ = flow
        video = history.get_task(task_id)["source_path"]
        history.update_task(task_id, {"transcription_path": None, "translation_path": None, "translations": {}})
        async def asr(identifier, _task):
            history.update_task(identifier, {"transcription_path": str(source)})
        async def dub(identifier, *_args):
            audio = source.with_suffix('.wav'); audio.write_bytes(b"fixture")
            history.update_task(identifier, {"dubbing_video_path": video, "dubbing_audio_path": str(audio)})
        transcribe = AsyncMock(side_effect=asr)
        translate = AsyncMock(return_value=TranscriptionResult(
            video_source="fixture.mp4", language="Chinese", segments=[
                TranscriptionSegment(start=0, end=2, text="新译文", confidence=1),
            ],
        ))
        monkeypatch.setattr(api.translation_service, "translate_full_result", translate)
        export, synthesize = AsyncMock(), AsyncMock(side_effect=dub)
        service = PipelineService(transcribe_stage=transcribe, export_stage=export, dub_stage=synthesize)
        monkeypatch.setattr(tasks, "pipeline_service", service)
        await service.start(task_id, "Chinese", "voice")
        state = history.get_task(task_id)
        assert state["status"] == "awaiting_review"
        transcribe.assert_awaited_once()
        translate.assert_awaited_once()
        export.assert_not_awaited()
        synthesize.assert_not_awaited()
        token = state["translation_reviews"]["Chinese"]["review_id"]
        await tasks.review_translation(task_id, tasks.TranslationReviewDecision(target_lang="Chinese", review_id=token))
        await service.start(task_id, "Chinese", "voice")
        assert history.get_task(task_id)["pipeline_status"] == "completed"
        transcribe.assert_awaited_once()
        translate.assert_awaited_once()
        export.assert_awaited_once()
        synthesize.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize("finish", ["success", "error", "cancel"])
def test_translation_reservation_blocks_edits_approval_and_other_starts_and_releases(flow, monkeypatch, finish):
    async def scenario():
        import app.api.translation as api
        import app.api.tasks as tasks
        history, task_id, source, _ = flow
        entered, release = asyncio.Event(), asyncio.Event()

        async def provider(**kwargs):
            entered.set()
            await release.wait()
            if finish == "error":
                raise RuntimeError("Provider failed")
            return kwargs["result"]

        monkeypatch.setattr(api.translation_service, "translate_full_result", provider)
        pending = asyncio.create_task(api.translate_transcription(str(source), "Chinese", task_id))
        await entered.wait()
        assert translation_busy(task_id)
        for operation in (
            lambda: tasks.edit_segment(task_id, 0, tasks.SegmentEdit(target_text="racing edit")),
            lambda: tasks.review_translation(task_id, tasks.TranslationReviewDecision(target_lang="Chinese")),
            lambda: api.translate_transcription(str(source), "Chinese", task_id),
        ):
            with pytest.raises(HTTPException) as error:
                await operation()
            assert error.value.status_code == 409
        with pytest.raises(RuntimeError, match="Translation is still processing"):
            PipelineService().start(task_id, "Chinese", "voice")
        if finish == "cancel":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            release.set()
            if finish == "error":
                with pytest.raises(HTTPException):
                    await pending
            else:
                await pending
        assert not translation_busy(task_id)
    asyncio.run(scenario())


def test_compression_requires_new_review_before_resynthesis(flow, monkeypatch, tmp_path):
    async def scenario():
        import app.api.tasks as tasks
        history, task_id, _, target = flow
        reviewer = TranslationReviewService(history)
        first = reviewer.request(task_id, "Chinese")
        reviewer.approve(task_id, "Chinese", first["review_id"])
        calls = []

        async def compact(identifier, language):
            data = json.loads(target.read_text(encoding="utf-8"))
            data["segments"][0]["text"] = "精简"
            target.write_text(json.dumps(data), encoding="utf-8")
            history.update_task(identifier, {"compression_attempts": 1, "compression_request": None})

        async def dub(*args):
            calls.append("dub")
            video = tmp_path / "compact.mp4"
            video.write_bytes(b"fixture")
            audio = video.with_suffix('.wav'); audio.write_bytes(b"fixture")
            history.update_task(task_id, {"dubbing_video_path": str(video), "dubbing_audio_path": str(audio)})

        service = PipelineService(export_stage=AsyncMock(), dub_stage=dub)
        monkeypatch.setattr(service, "_compress_translation", compact)
        monkeypatch.setattr(tasks, "pipeline_service", service)
        await service.start(task_id, "Chinese", "voice", compress_translation=True)
        assert history.get_task(task_id)["pipeline_status"] == "awaiting_review"
        assert calls == []
        review = history.get_task(task_id)["translation_reviews"]["Chinese"]
        assert review["review_id"] != first["review_id"]
        reviewer.approve(task_id, "Chinese", review["review_id"])
        await service.start(task_id, "Chinese", "voice")
        assert calls == ["dub"]
        assert history.get_task(task_id)["compression_attempts"] == 1
    asyncio.run(scenario())


def test_pipeline_endpoint_requires_exact_token_before_start(flow, monkeypatch):
    async def scenario():
        import app.api.pipeline as api
        history, task_id, _, _ = flow
        reviewer = TranslationReviewService(history)
        reviewer.pause(task_id, "Chinese")
        token = history.get_task(task_id)["translation_reviews"]["Chinese"]["review_id"]
        monkeypatch.setattr(api, "resolve_tts_route", lambda *_: Mock(provider_id="qwen"))
        monkeypatch.setattr(api, "resolve_available_voice", AsyncMock(return_value="voice"))
        start = Mock()
        monkeypatch.setattr(api.pipeline_service, "start", start)
        with pytest.raises(HTTPException) as error:
            await api.start_pipeline(api.PipelineRequest(task_id=task_id, review_id="old-token"))
        assert error.value.status_code == 409
        start.assert_not_called()
        assert not reviewer.is_approved(task_id, "Chinese")
        await api.start_pipeline(api.PipelineRequest(task_id=task_id, review_id=token))
        start.assert_called_once()
        assert reviewer.is_approved(task_id, "Chinese")
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["dubbing", "burn"])
def test_active_standalone_media_blocks_translation_and_review_changes(flow, stage):
    async def scenario():
        import app.api.translation as api
        import app.api.tasks as tasks
        history, task_id, source, target = flow
        history.update_task(task_id, {f"{stage}_status": "processing"})
        for operation in (
            lambda: api.translate_transcription(str(source), "Chinese", task_id),
            lambda: tasks.edit_segment(task_id, 0, tasks.SegmentEdit(target_text="racing edit")),
            lambda: tasks.review_translation(task_id, tasks.TranslationReviewDecision(target_lang="Chinese")),
            lambda: api.export_subtitles(str(target), "srt", task_id, "translated", download=False),
        ):
            with pytest.raises(HTTPException) as error:
                await operation()
            assert error.value.status_code == 409
        assert not translation_busy(task_id)
    asyncio.run(scenario())


def test_review_snapshot_is_terminal_and_contains_exact_current_token(flow):
    async def scenario():
        from app.core.events import event_generator, task_event_snapshot
        history, task_id, _, _ = flow
        TranslationReviewService(history).pause(task_id, "Chinese")
        state = history.get_task(task_id)
        stream = event_generator(task_id, initial_data=task_event_snapshot(state), snapshot_loader=lambda: history.get_task(task_id))
        payload = await anext(stream)
        event = json.loads(payload.removeprefix("data: "))
        assert event["translation_reviews"] == state["translation_reviews"]
        assert event["pipeline_status"] == "awaiting_review"
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 0.5)
    asyncio.run(scenario())


def test_shared_timing_edit_invalidates_review_without_retranslating(flow):
    async def scenario():
        import app.api.tasks as tasks
        history, task_id, source, target = flow
        reviewer = TranslationReviewService(history)
        first = reviewer.request(task_id, "Chinese")
        reviewer.approve(task_id, "Chinese", first["review_id"])
        await tasks.edit_segment(task_id, 0, tasks.SegmentEdit(start=0.25, end=1.75))
        state = history.get_task(task_id)
        assert state["status"] == "awaiting_review"
        assert state["translation_reviews"]["Chinese"]["review_id"] != first["review_id"]
        for path in (source, target):
            row = json.loads(path.read_text(encoding="utf-8"))["segments"][0]
            assert (row["start"], row["end"]) == (0.25, 1.75)
        assert not reviewer.is_approved(task_id, "Chinese")
    asyncio.run(scenario())


@pytest.mark.parametrize("edit_during_discovery", [False, True])
def test_direct_dubbing_rechecks_after_voice_discovery_and_reserves_worker_gap(flow, monkeypatch, edit_during_discovery):
    async def scenario():
        import app.api.dubbing as api
        import app.api.tasks as tasks
        history, task_id, _, _ = flow
        reviewer = TranslationReviewService(history)
        review = reviewer.request(task_id, "Chinese")
        reviewer.approve(task_id, "Chinese", review["review_id"])
        monkeypatch.setattr(api, "resolve_tts_route", lambda *_: Mock(provider_id="qwen"))
        entered, release = asyncio.Event(), asyncio.Event()
        async def voice(*args):
            entered.set()
            await release.wait()
            return "voice"
        monkeypatch.setattr(api, "resolve_available_voice", voice)
        background = BackgroundTasks()
        request = api.DubbingRequest(task_id=task_id, voice="voice")
        pending = asyncio.create_task(api.start_dubbing(request, background))
        await entered.wait()
        if edit_during_discovery:
            await tasks.edit_segment(task_id, 0, tasks.SegmentEdit(target_text="new edit"))
        release.set()
        if edit_during_discovery:
            with pytest.raises(HTTPException) as error:
                await pending
            assert error.value.status_code == 409
            assert not background.tasks
            assert history.get_task(task_id)["pipeline_status"] == "awaiting_review"
        else:
            await pending
            assert history.get_task(task_id)["dubbing_status"] == "processing"
            assert len(background.tasks) == 1
            with pytest.raises(HTTPException) as error:
                await tasks.edit_segment(task_id, 0, tasks.SegmentEdit(target_text="racing edit"))
            assert error.value.status_code == 409
    asyncio.run(scenario())
