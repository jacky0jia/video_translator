"""No models, GPU, network or user history are touched by these tests."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest

from app.core.history import HistoryManager
from app.core.task_run import TaskRun, task_run_scope
from app.services.translation_review_service import (
    TranslationReviewConflict, TranslationReviewService,
    TranslationReviewUnavailable, translation_fingerprint,
)


@pytest.fixture
def review_case(tmp_path):
    history = HistoryManager(tmp_path / "history.json")
    task_id = history.create_task("fixture.mp4", "local", target_lang="Chinese")
    source = tmp_path / "source.json"
    target = tmp_path / "translated.json"
    source.write_text(json.dumps({"segments": [
        {"start": 0, "end": 2, "text": "Hello", "confidence": None},
    ]}), encoding="utf-8")
    target.write_text(json.dumps({"segments": [
        {"start": 0, "end": 2, "text": "你好", "confidence": None},
    ]}), encoding="utf-8")
    history.update_task(task_id, {"transcription_path": str(source),
                                 "translation_path": str(target),
                                 "translations": {"Chinese": str(target)}})
    return history, TranslationReviewService(history), task_id, source, target


def test_pending_and_approval_survive_new_manager(review_case):
    history, service, task_id, _, _ = review_case
    review = service.request(task_id, "Chinese")
    assert not service.is_approved(task_id, "Chinese")
    reloaded = TranslationReviewService(HistoryManager(history.history_file))
    assert reloaded.approve(task_id, "Chinese", review["review_id"])["status"] == "approved"
    assert service.is_approved(task_id, "Chinese")
    revision = history.get_task(task_id)["state_revision"]
    reloaded.approve(task_id, "Chinese", review["review_id"])
    assert history.get_task(task_id)["state_revision"] == revision


def test_legacy_translation_is_not_implicitly_approved(review_case):
    _, service, task_id, _, _ = review_case
    assert not service.is_approved(task_id, "Chinese")
    with pytest.raises(TranslationReviewConflict):
        service.approve(task_id, "Chinese", "made-up")


def test_review_metadata_does_not_start_workflow_or_remove_outputs(review_case):
    history, service, task_id, _, _ = review_case
    history.update_task(task_id, {"status": "completed", "pipeline_status": "completed",
                                 "subtitle_outputs": {"srt": "existing.srt"},
                                 "dubbing_video_path": "existing.mp4"})
    before = history.get_task(task_id)
    review = service.request(task_id, "Chinese")
    service.approve(task_id, "Chinese", review["review_id"])
    after = history.get_task(task_id)
    assert {key: value for key, value in after.items()
            if key not in {"translation_reviews", "state_revision"}} == {
                key: value for key, value in before.items() if key != "state_revision"}


def test_identical_retranslation_requires_new_decision(review_case):
    _, service, task_id, _, _ = review_case
    first = service.request(task_id, "Chinese")
    service.approve(task_id, "Chinese", first["review_id"])
    second = service.request(task_id, "Chinese")
    assert first["fingerprint"] == second["fingerprint"]
    assert first["review_id"] != second["review_id"]
    assert not service.is_approved(task_id, "Chinese")
    with pytest.raises(TranslationReviewConflict):
        service.approve(task_id, "Chinese", first["review_id"])


@pytest.mark.parametrize("document", ["source", "target"])
@pytest.mark.parametrize("field,value", [("text", "Manual edit"), ("start", 0.25), ("end", 3)])
def test_edit_invalidates_approval_and_old_token(review_case, document, field, value):
    _, service, task_id, source, target = review_case
    old = service.request(task_id, "Chinese")
    service.approve(task_id, "Chinese", old["review_id"])
    path = source if document == "source" else target
    data = json.loads(path.read_text(encoding="utf-8"))
    data["segments"][0][field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not service.is_approved(task_id, "Chinese")
    with pytest.raises(TranslationReviewConflict):
        service.approve(task_id, "Chinese", old["review_id"])
    current = service.request(task_id, "Chinese")
    service.approve(task_id, "Chinese", current["review_id"])
    assert service.is_approved(task_id, "Chinese")
    assert json.loads(path.read_text(encoding="utf-8"))["segments"][0][field] == value


def test_fingerprint_ignores_json_formatting_but_binds_task_and_language(review_case):
    history, _, task_id, source, _ = review_case
    task = history.get_task(task_id)
    original = translation_fingerprint(task, "Chinese")
    document = json.loads(source.read_text(encoding="utf-8"))
    source.write_text(json.dumps(document, indent=4, sort_keys=True), encoding="utf-8-sig")
    assert translation_fingerprint(task, "Chinese") == original
    assert translation_fingerprint({**task, "task_id": "another"}, "Chinese") != original
    task["translations"]["Japanese"] = task["translation_path"]
    assert translation_fingerprint(task, "Japanese") != original


def test_language_reviews_are_independent_and_tokens_cannot_cross_languages(review_case):
    history, service, task_id, _, target = review_case
    history.update_task(task_id, {"translations": {"Chinese": str(target), "Japanese": str(target)}})
    chinese = service.request(task_id, "Chinese")
    japanese = service.request(task_id, "Japanese")
    with pytest.raises(TranslationReviewConflict):
        service.approve(task_id, "Japanese", chinese["review_id"])
    service.approve(task_id, "Chinese", chinese["review_id"])
    assert service.is_approved(task_id, "Chinese")
    assert not service.is_approved(task_id, "Japanese")
    service.approve(task_id, "Japanese", japanese["review_id"])
    assert service.is_approved(task_id, "Japanese")
    service.request(task_id, "Chinese")
    assert service.is_approved(task_id, "Japanese")


@pytest.mark.parametrize("contents", [
    "not json", "null", "[]", "{}", '{"segments": null}',
    '{"segments": [null]}', '{"segments": [{"start": 0, "end": 2, "text": null}]}',
    '{"segments": [{"start": true, "end": 2, "text": "x"}]}',
    '{"segments": [{"start": -1, "end": 2, "text": "x"}]}',
    '{"segments": [{"start": 2, "end": 2, "text": "x"}]}',
    '{"segments": [{"start": 3, "end": 2, "text": "x"}]}',
    '{"segments": [{"start": 0, "end": NaN, "text": "x"}]}',
    '{"segments": [{"start": 0, "end": 2, "text": "x", "other": Infinity}]}',
    '{"segments": []}',
])
def test_invalid_documents_fail_closed_without_modification(review_case, contents):
    history, service, task_id, _, target = review_case
    before = deepcopy(history.get_task(task_id))
    target.write_text(contents, encoding="utf-8")
    assert not service.is_approved(task_id, "Chinese")
    with pytest.raises(TranslationReviewUnavailable):
        service.request(task_id, "Chinese")
    assert history.get_task(task_id) == before
    assert target.read_text(encoding="utf-8") == contents


def test_missing_file_and_wrong_language_cannot_reuse_approval(review_case):
    history, service, task_id, _, target = review_case
    review = service.request(task_id, "Chinese")
    service.approve(task_id, "Chinese", review["review_id"])
    assert not service.is_approved(task_id, "Japanese")
    target.unlink()
    assert not service.is_approved(task_id, "Chinese")
    assert history.get_task(task_id)["translation_reviews"]["Chinese"]["status"] == "approved"
    with pytest.raises(TranslationReviewUnavailable):
        service.approve(task_id, "Chinese", review["review_id"])


@pytest.mark.parametrize("operation", ["request", "approve"])
def test_concurrent_history_change_rejects_decision_without_overwriting_it(review_case, monkeypatch, operation):
    history, service, task_id, _, _ = review_case
    review = service.request(task_id, "Chinese")
    original = history.compare_and_update_task

    def intervening_write(identifier, revision, changes):
        history.update_task(identifier, {"message": "Newer worker state"})
        return original(identifier, revision, changes)

    monkeypatch.setattr(history, "compare_and_update_task", intervening_write)
    with pytest.raises(TranslationReviewConflict):
        if operation == "request":
            service.request(task_id, "Chinese")
        else:
            service.approve(task_id, "Chinese", review["review_id"])
    task = history.get_task(task_id)
    assert task["message"] == "Newer worker state"
    assert task["translation_reviews"]["Chinese"] == review


def test_obsolete_pipeline_cannot_request_or_approve_review(review_case):
    history, service, task_id, _, _ = review_case
    review = service.request(task_id, "Chinese")
    history.update_task(task_id, {"pipeline_run_id": "new"})
    for active in (True, False):
        with task_run_scope(TaskRun(task_id, "old", active=active)):
            with pytest.raises(TranslationReviewConflict):
                service.request(task_id, "Chinese")
            with pytest.raises(TranslationReviewConflict):
                service.approve(task_id, "Chinese", review["review_id"])
    assert not service.is_approved(task_id, "Chinese")


def test_missing_task_is_not_created_by_review(review_case):
    history, service, _, _, _ = review_case
    assert not service.is_approved("missing", "Chinese")
    with pytest.raises(TranslationReviewUnavailable):
        service.request("missing", "Chinese")
    assert len(history.get_all_tasks()) == 1


def test_no_subtitle_rows_cannot_be_approved(review_case):
    _, service, task_id, source, target = review_case
    for path in (source, target):
        path.write_text('{"segments": []}', encoding="utf-8")
    with pytest.raises(TranslationReviewUnavailable):
        service.request(task_id, "Chinese")


def test_legacy_single_translation_path_is_language_scoped(review_case):
    history, service, task_id, _, _ = review_case
    history.update_task(task_id, {"translations": {}})
    review = service.request(task_id, "Chinese")
    service.approve(task_id, "Chinese", review["review_id"])
    assert service.is_approved(task_id, "Chinese")
    with pytest.raises(TranslationReviewUnavailable):
        service.request(task_id, "Japanese")


def test_first_revision_and_current_run_are_supported(tmp_path):
    history = HistoryManager(tmp_path / "history.json")
    task_id = history.create_task("fixture.mp4", "local")
    assert history.compare_and_update_task(task_id, 0, {"pipeline_run_id": "current"})
    assert not history.compare_and_update_task(task_id, 0, {"message": "old"})
    with task_run_scope(TaskRun(task_id, "current")):
        state = history.compare_and_update_task(task_id, 1, {"state_revision": -100, "message": "current"})
    assert state["state_revision"] == 2
    assert state["message"] == "current"
    assert history.compare_and_update_task("missing", 0, {"message": "no task"}) is None


def test_revision_compare_and_swap_is_shared_between_managers(review_case):
    history, _, task_id, _, _ = review_case
    second = HistoryManager(history.history_file)
    revision = history.get_task(task_id)["state_revision"]
    barrier = Barrier(2)

    def attempt(manager, value):
        barrier.wait()
        return manager.compare_and_update_task(task_id, revision, {"message": value})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt, history, "first")
        other = pool.submit(attempt, second, "second")
        results = [first.result(), other.result()]
    assert sum(bool(result) for result in results) == 1
    assert history.get_task(task_id)["state_revision"] == revision + 1


@pytest.mark.parametrize("revision", [-1, True, "1", None, 1.5])
def test_invalid_expected_revision_does_not_write(review_case, revision):
    history, _, task_id, _, _ = review_case
    before = history.get_task(task_id)
    with pytest.raises(ValueError):
        history.compare_and_update_task(task_id, revision, {"message": "bad"})
    assert history.get_task(task_id) == before
