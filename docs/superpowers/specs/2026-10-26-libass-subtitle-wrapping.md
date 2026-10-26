# Libass Subtitle Wrapping Design

## Problem

Long burned-in subtitles can extend beyond both sides of the video. The ASS
output currently combines `WrapStyle: 1` with an explicit `\pos` anchor. In
libass, positioned dialogue is not constrained by the style margins, so a long
line can remain on one line and be clipped. The existing render regression test
looks up FFmpeg only on `PATH`; packaged installations use the bundled FFmpeg,
which allowed the relevant test to be skipped.

## Chosen behavior

Use normal bottom-aligned ASS dialogue and let libass wrap against the real font
metrics and left/right style margins. Preserve the current source/target block,
font styling, subtitle offset, and timing. Let libass perform collision handling
for the uncommon case where cues overlap in time.

This deliberately removes fixed `\pos` placement from final ASS events. During
an overlap, libass may move a cue and let it fall back after the other cue ends;
that is the accepted tradeoff for reliable width-constrained rendering.

## Verification

- Render an overlong Chinese cue with the configured bundled FFmpeg and prove
  its visible pixels remain inside the video and span multiple lines.
- Verify bilingual and extra-language tracks remain in one stacked dialogue.
- Verify overlapping cues remain separately visible without glyph collision.
- Keep the pure placement helpers and browser preview behavior unchanged.
- Run the complete Python suite and frontend build/tests before delivery.

