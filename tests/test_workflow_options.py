"""Submitted options survive review/reload without claiming execution success."""
import asyncio
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.history import HistoryManager


@pytest.fixture
def case(tmp_path, monkeypatch):
    import app.api.tasks as api
    history = HistoryManager(tmp_path / 'history.json')
    task_id = history.create_task('fixture.mp4', 'local', target_lang='Chinese')
    history.update_task(task_id, {'status': 'awaiting_review', 'pipeline_status': 'awaiting_review',
        'translation_reviews': {'Chinese': {'review_id': 'keep', 'status': 'pending'}},
        'dubbing_review': {'status': 'required', 'rows': [1]}, 'compression_attempts': 2})
    monkeypatch.setattr(api, 'history_manager', history)
    monkeypatch.setattr(api.pipeline_service, 'is_busy', Mock(return_value=False))
    return api, history, task_id


def test_saved_options_survive_disk_reload_and_do_not_approve_or_clear_reviews(case):
    api, history, task_id = case
    before = history.get_task(task_id)
    request = api.WorkflowOptions(route='dubbing', voice='voice-2', speed=1.2, subtitle_format='ass',
        subtitle_content='bilingual', burn_subtitles=True, end_note_enabled=False,
        show_source=False, subtitle_style={'fontSize': 30},
        output_options={'policy': 'custom', 'codec': 'h264', 'resolution': '1080', 'bitrate_mbps': 8})
    result = asyncio.run(api.save_workflow_options(task_id, request))
    after = HistoryManager(history.history_file).get_task(task_id)
    assert after['workflow_options'] == request.model_dump() == result['workflow_options']
    assert after['state_revision'] == before['state_revision'] + 1
    for key in ('status', 'pipeline_status', 'translation_reviews', 'dubbing_review', 'compression_attempts'):
        assert after[key] == before[key]
    assert asyncio.run(api.get_task_details(task_id))['task']['workflow_options'] == request.model_dump()
    assert asyncio.run(api.list_tasks())['tasks'][0]['workflow_options'] == request.model_dump()


@pytest.mark.parametrize('busy', [
    {'pipeline_status': 'processing'}, {'pipeline_status': 'cancelling'},
    {'pipeline_status': 'awaiting_compression'}, {'pipeline_cleanup_pending': True},
    {'dubbing_status': 'processing'}, {'burn_status': 'processing'},
])
def test_busy_tasks_cannot_have_options_replaced(case, busy):
    api, history, task_id = case
    history.update_task(task_id, busy)
    before = history.get_task(task_id)
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.save_workflow_options(task_id, api.WorkflowOptions()))
    assert error.value.status_code == 409
    assert history.get_task(task_id) == before


def test_live_worker_and_translation_reservation_block_option_save(case, monkeypatch):
    api, history, task_id = case
    before = history.get_task(task_id)
    for worker, translating in ((True, False), (False, True)):
        monkeypatch.setattr(api.pipeline_service, 'is_busy', Mock(return_value=worker))
        monkeypatch.setattr(api, 'translation_busy', lambda _: translating)
        with pytest.raises(HTTPException) as error:
            asyncio.run(api.save_workflow_options(task_id, api.WorkflowOptions()))
        assert error.value.status_code == 409
        assert history.get_task(task_id) == before


def test_missing_task_and_compare_conflict_are_not_reported_as_saved(case, monkeypatch):
    api, history, task_id = case
    with pytest.raises(HTTPException) as missing:
        asyncio.run(api.save_workflow_options('missing', api.WorkflowOptions()))
    assert missing.value.status_code == 404
    before = history.get_task(task_id)
    monkeypatch.setattr(history, 'compare_and_update_task', Mock(return_value=False))
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(api.save_workflow_options(task_id, api.WorkflowOptions()))
    assert conflict.value.status_code == 409
    assert history.get_task(task_id) == before


@pytest.mark.parametrize('invalid', [
    {'speed': None}, {'speed': 'NaN'}, {'speed': 100}, {'route': 'unknown'},
    {'subtitle_format': 'exe'}, {'subtitle_content': 'unknown'},
    {'output_options': {'policy': 'unknown'}}, {'status': 'completed'},
])
def test_invalid_options_are_rejected_at_http_boundary(case, invalid):
    api, history, task_id = case
    app = FastAPI()
    app.include_router(api.router)
    before = history.get_task(task_id)
    with TestClient(app) as client:
        response = client.post(f'/tasks/{task_id}/workflow-options', json=invalid)
    assert response.status_code == 422
    assert history.get_task(task_id) == before
