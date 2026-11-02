# Libass Subtitle Wrapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep long burned-in subtitles fully inside the video by restoring libass width-constrained wrapping.

**Architecture:** Keep the stable ASS placement model and render through FFmpeg's Unicode-aware `subtitles` filter. Use the same renderer for overlap height measurement so layout and final burn agree.

**Tech Stack:** Python, ASS/libass, bundled FFmpeg, pytest

**Spec:** `docs/superpowers/specs/2026-10-26-libass-subtitle-wrapping.md`

## Global Constraints

- Every behavior change must be covered by a test that fails before implementation.
- Use the configured bundled FFmpeg for render verification instead of relying on `PATH`.
- Preserve subtitle text, timing, source/target order, font styling, and vertical offset.
- Commit every completed change and leave unrelated working-tree files untouched.

## Review Focus

- Long unspaced CJK text must wrap without touching either horizontal edge.
- Long Latin text and mixed punctuation must use the same safe margins.
- Bilingual and extra-language rows must remain ordered within one dialogue.
- Overlapping cues must remain legible when libass collision handling is active.
- Vertical offset must still move the subtitle block upward by the configured amount.

---

### Task 1: Enable Unicode-aware renderer wrapping

**Files:**
- Modify: `tests/test_subtitle_layout.py`
- Modify: `tests/test_subtitle_display.py`
- Modify: `app/services/video_burn_service.py`
- Modify: `app/services/subtitle_measurement.py`

**Interfaces:**
- Consumes: `SubtitleFormatter.to_ass(...) -> str` and `settings.ffmpeg_path`
- Produces: Unicode-aware final rendering and overlap measurement while preserving stable `\pos` placement

- [x] **Step 1: Write the failing render regression test**

Add a real bundled-FFmpeg test for an overlong unspaced Chinese cue at 854x480. Assert that rendered pixels stay away from both edges and occupy at least two line bands. Add a real measurement test proving the same cue reports multiline height, and assert both hardware and fallback burn commands enable Unicode wrapping.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_subtitle_layout.py tests/test_subtitle_display.py -q`

Expected: the production burn command lacks Unicode wrapping and overlap measurement reports only one line.

- [x] **Step 3: Enable Unicode-aware rendering**

Render final ASS files with `subtitles=...:wrap_unicode=1` and use the same filter in overlap measurement. Preserve the current formatter positioning and styling.

- [x] **Step 4: Run focused and full verification**

Run the focused Python tests, the full Python suite, frontend tests, and frontend production build. Expected: all pass without warnings attributable to this change.

- [x] **Step 5: Commit**

Commit the formatter and regression tests with a message describing restored libass wrapping.

