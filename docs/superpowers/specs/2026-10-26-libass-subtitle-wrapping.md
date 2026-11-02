# Libass Subtitle Wrapping Design

## Problem

Long burned-in subtitles can extend beyond both sides of the video. The native
FFmpeg `ass` filter disables Unicode line breaking for ASS input, so an unspaced
Chinese line remains on one line and can be clipped. The existing render
regression test looks up FFmpeg only on `PATH`; packaged installations use the
bundled FFmpeg, which allowed the relevant test to be skipped.

## Chosen behavior

Render ASS files through FFmpeg's `subtitles` filter with `wrap_unicode=1`,
which applies the Unicode Line Breaking Algorithm using real font metrics and
the existing left/right style margins. Preserve the current source/target
block, font styling, subtitle offset, timing, and fixed overlap placement.

Use the same Unicode-aware renderer while measuring overlapping blocks so the
measured height matches the final burn.

## Verification

- Render an overlong Chinese cue with the configured bundled FFmpeg and prove
  its visible pixels remain inside the video and span multiple lines.
- Verify an overlong Chinese cue reports a multiline height during overlap
  measurement.
- Verify bilingual and extra-language tracks remain in one stacked dialogue.
- Verify overlapping cues remain separately visible without glyph collision.
- Keep the pure placement helpers and browser preview behavior unchanged.
- Run the complete Python suite and frontend build/tests before delivery.

