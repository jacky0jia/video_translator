# Libass Subtitle Wrapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep long burned-in subtitles fully inside the video by restoring libass width-constrained wrapping.

**Architecture:** Generate ordinary bottom-aligned ASS dialogue without fixed `\pos` coordinates. Keep source and translated rows in one styled dialogue and rely on libass for real-font wrapping and collision handling.

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

### Task 1: Restore renderer-managed wrapping

**Files:**
- Modify: `tests/test_subtitle_layout.py`
- Modify: `tests/test_subtitle_display.py`
- Modify: `app/services/formatter_service.py`

**Interfaces:**
- Consumes: `SubtitleFormatter.to_ass(...) -> str` and `settings.ffmpeg_path`
- Produces: ASS dialogue anchored by alignment and margins, without `\pos`

- [ ] **Step 1: Write the failing render regression test**

Add a real bundled-FFmpeg test for an overlong unspaced Chinese cue at 854x480. Assert that rendered pixels stay away from both edges and occupy at least two line bands. Update the overlap test to require legible, separate cues rather than fixed coordinates.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_subtitle_layout.py tests/test_subtitle_display.py -q`

Expected: the long cue reaches the frame edges or remains one line under the current fixed-position output.

- [ ] **Step 3: Implement normal bottom-aligned ASS dialogue**

In `SubtitleFormatter.to_ass`, remove fixed coordinate positioning and rendered-height placement from the final ASS event generation. Convert the existing 80-pixel baseline plus scaled user offset into the style's vertical margin, preserve `WrapStyle: 1`, and emit each display block with bottom alignment so libass applies the horizontal margins and collision handling.

- [ ] **Step 4: Run focused and full verification**

Run the focused Python tests, the full Python suite, frontend tests, and frontend production build. Expected: all pass without warnings attributable to this change.

- [ ] **Step 5: Commit**

Commit the formatter and regression tests with a message describing restored libass wrapping.

