# ASR Context and Subtitle Segmentation Design

## Intent

Resolve H05's repeated or boundary hallucinations by giving local
faster-whisper enough audio context, while making the user-facing setting
control the length of displayed subtitle fragments instead of the model's
private decoding window.

## Goals

- Local faster-whisper always decodes with a 30-second context window and
  word timestamps.
- Replace `ASR_CHUNK_LENGTH` in settings with `SUBTITLE_SEGMENT_SECONDS`, shown
  as **Subtitle segment duration (seconds)** / **字幕分片时间（秒）**.
- Default the subtitle segment duration to 8 seconds.
- Split long recognized speech at word boundaries, preferring punctuation or
  a natural segment boundary at or before the configured duration.
- Preserve every recognized word, its order, and monotonic timestamps. Never
  delete text merely because it looks like a common hallucination.
- Keep the existing advisory highlighting for suspicious repeated and media
  boundary rows.

## Configuration migration

Configuration schema version 2 removes `ASR_CHUNK_LENGTH`. An existing
positive value is migrated to `SUBTITLE_SEGMENT_SECONDS`, so a saved value of
6 or 8 keeps the subtitle rhythm the user expected. If the old key is absent,
the new value defaults to 8. The supported range is 1 through 30 seconds.
Migration is idempotent and the obsolete key is not written back.

## Processing design

Both the in-process and worker-process local ASR paths pass
`chunk_length=30` and `word_timestamps=True` to faster-whisper. After decoding,
a shared pure function converts faster-whisper segments into application
subtitle segments:

1. Keep an upstream segment when it is within the configured duration.
2. For a longer segment with word timestamps, choose the latest punctuation
   boundary that fits. If none exists, choose the latest word ending within
   the duration.
3. Start the next fragment at its first word and repeat until all words are
   consumed.
4. If word timestamps are missing or invalid, keep the upstream segment
   unchanged rather than estimating or dropping speech.

Confidence for each output fragment remains the originating segment's
average log probability. Manual source edits and existing review hints keep
their current behavior.

Remote OpenAI-compatible transcription remains unchanged because its current
contract provides segment timestamps but no reliable word timestamps.

## Approaches considered

1. **Fixed 30-second context plus duration-based subtitle segmentation —
   selected.** It matches the user's mental model, removed the observed ghost
   run in isolated tests, and separates recognition quality from presentation.
2. Change only the ASR window default. Existing 6/8-second configurations
   would keep the problematic behavior and the setting would remain
   misleading.
3. Use `hallucination_silence_threshold`. It removed one boundary example but
   changed valid segmentation and introduced other short repetitions in the
   long sample, so it is not enabled.

## Validation

- Unit tests cover versioned configuration migration and settings metadata.
- Unit tests cover punctuation splitting, word fallback, multilingual
  punctuation, missing/invalid word timestamps, exact-duration segments, and
  full text/timestamp preservation.
- Service and worker tests prove both local paths request 30-second context and
  word timestamps; remote transcription remains unchanged.
- Full Python and frontend regression suites must pass.
- The isolated 8-minute-30-second sample is rerun to check that the 22 repeated
  `Thanks for watching.` rows remain absent, with no new overlap or dropped
  recognized text.

## Non-goals

- No CTranslate2 or faster-whisper dependency upgrade.
- No phrase blacklist or automatic deletion based on confidence alone.
- No global `hallucination_silence_threshold` default.
- No change to translation, dubbing, or subtitle rendering layout.
