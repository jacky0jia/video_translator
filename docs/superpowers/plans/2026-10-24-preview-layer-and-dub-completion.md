# Preview Layer and Dub Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix modal/subtitle stacking and make the Dub stage turn green at the exact dubbing-completion event.

**Architecture:** Keep overlay stacking local to the video component. Extend the existing persisted SSE snapshot contract with verified dubbing artifact metadata so the current frontend completion predicate succeeds immediately on `dubbing_completed`.

**Tech Stack:** React, Tailwind CSS, FastAPI/Python SSE, pytest/unittest

**Spec:** `docs/superpowers/specs/2026-10-24-preview-layer-and-dub-completion.md`

## Global Constraints

- Every completed modification must include updated or new tests.
- All relevant tests and validation must pass before delivery.
- Every completed modification must be committed to Git.
- Do not infer Dub completion solely from Render starting.

## Review Focus

- Settings and nested path dialogs must remain above preview subtitles.
- Full-screen preview controls and subtitles must remain visible inside the video.
- A `dubbing_completed` event must contain both audio and raw/video output paths.
- Browser-safe URL conversion must still apply to artifact paths in SSE frames.
- Standalone dubbing and full dubbing pipelines must use the same completion contract.

---

### Task 1: Constrain Preview Subtitle Stacking

**Files:**
- Modify: `app/frontend/src/components/VideoPreview.jsx`
- Test: `tests/test_preview_modal_layering.py`

**Interfaces:**
- Consumes: `.settings-overlay` modal layer (`z-index: 50`)
- Produces: `[data-subtitle-overlay]` constrained to the video stacking layer

- [ ] **Step 1: Write the failing test**

Add `test_preview_subtitles_do_not_outrank_settings_modal`, asserting that the overlay no longer uses `z-[9999]` and uses a local layer below the modal.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preview_modal_layering.py -v`
Expected: FAIL because `VideoPreview.jsx` contains `z-[9999]`.

- [ ] **Step 3: Implement the minimal stacking fix**

Replace the global subtitle layer with the existing local video overlay layer while preserving pointer-event and full-screen behavior.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preview_modal_layering.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit `VideoPreview.jsx` and its regression test.

### Task 2: Publish an Authoritative Dub Completion Event

**Files:**
- Modify: `app/core/events.py`
- Test: `tests/test_pipeline_ui_sync.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: persisted dubbing status, target language, and artifact paths
- Produces: `task_event_snapshot(task: dict) -> dict` containing browser-serializable dubbing completion evidence

- [ ] **Step 1: Write the failing tests**

Assert that task event snapshots include `dubbing_target_lang`, `dubbing_audio_path`, `dubbing_video_path`, and `dubbing_raw_video_path`, and that SSE serialization converts local artifact paths to output URLs.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline_ui_sync.py tests/test_events.py -v`
Expected: FAIL because the artifact fields are absent from `TASK_EVENT_FIELDS`.

- [ ] **Step 3: Implement the event contract**

Add the four dubbing completion evidence fields to `TASK_EVENT_FIELDS`. Keep the existing `_browser_event` path conversion and `dubbing_completed` emission point.

- [ ] **Step 4: Run focused workflow verification**

Run: `pytest tests/test_pipeline_ui_sync.py tests/test_events.py tests/test_task_ui_contract.py tests/test_workflow_progress.py -v`
Expected: PASS.

- [ ] **Step 5: Run frontend build and full automated tests**

Run the repository's frontend build and full pytest suite.
Expected: all commands succeed.

- [ ] **Step 6: Commit**

Commit `events.py` and its regression tests.

