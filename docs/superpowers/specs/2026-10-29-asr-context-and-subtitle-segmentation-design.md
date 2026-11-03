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

The local paths do not add another VAD pass or overlapping audio windows in
this change. Faster-whisper remains responsible for advancing through the
offline file, while the application only reshapes its timestamped output.
This avoids duplicating words at artificial overlap boundaries and avoids
changing the source timeline through a second silence-removal mapping.

Remote OpenAI-compatible transcription remains unchanged because its current
contract provides segment timestamps but no reliable word timestamps.

## VLC mechanism review

The VideoLAN reference was checked on 2026-10-29 against the current official
draft merge request
[`videolan/vlc!5155`](https://code.videolan.org/videolan/vlc/-/merge_requests/5155),
source revision `7d6a2efb8daa2720d0e585e71b1843c90b968d82`. The merge request is still open
and marked draft, so it is useful implementation evidence rather than a
released VLC behavior guarantee.

The draft implements live speech-to-text as a subtitle decoder and separates
recognition buffering from subtitle presentation:

- Audio is converted to 16 kHz mono float samples and queued until the
  configured recognition window is available. The window defaults to 30,000
  milliseconds and supports 5,000 through 40,000 milliseconds.
- Whisper token timestamps are enabled, splitting is constrained to word
  boundaries, and Whisper is asked to keep generated subtitle segments within
  84 characters. Thus the 30-second audio window does not become one
  30-second on-screen subtitle.
- The live pipeline applies VAD, uses a 0.2 threshold, and carries 200
  milliseconds of audio into the next window to protect streaming boundaries.
- Seeking or another discontinuity flushes the converted-audio FIFO and aborts
  the in-flight Whisper call before playback resumes.

The first two points support this design's central separation between model
context and displayed subtitle size. The other details are deliberately not
copied: this application transcribes a complete offline file rather than a
live playback stream, the user selected a time-based subtitle rhythm rather
than VLC's fixed character ceiling, and the isolated 30-second/word-timestamp
run already removed the observed ghost sequence without another VAD or overlap
layer. Seek/flush handling is therefore outside H05's offline transcription
path.

For reproducibility, the reviewed plain diff is stored outside the application
tree at
`I:\AI Coding\codex\subtitle_translator\.codex-test-runs\vlc-stt-mr5155\mr5155.diff`
with SHA-256
`39DE8AE9CCF2797B25B5B742EA35D04747DA5BF903C6A349C03B030D7D9954E8`.

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
4. Copy VLC's live VAD, 200-millisecond overlap, and 84-character ceiling.
   Those controls solve live buffering and display constraints, while this
   application has an offline timeline and a user-selected duration. Adding
   them would introduce duplicate-boundary and timestamp-remapping risks
   without evidence that they are needed for the observed H05 sample.

## Validation

- Unit tests cover versioned configuration migration and settings metadata.
- Unit tests cover punctuation splitting, word fallback, multilingual
  punctuation, missing/invalid word timestamps, exact-duration segments, and
  full text/timestamp preservation.
- Service and worker tests prove both local paths request 30-second context and
  word timestamps, without adding a second VAD or overlap layer; remote
  transcription remains unchanged.
- Full Python and frontend regression suites must pass.
- The isolated 8-minute-30-second sample is rerun to check that the 22 repeated
  `Thanks for watching.` rows remain absent, with no new overlap or dropped
  recognized text.

## Non-goals

- No CTranslate2 or faster-whisper dependency upgrade.
- No phrase blacklist or automatic deletion based on confidence alone.
- No global `hallucination_silence_threshold` default.
- No VLC-style external VAD, rolling audio overlap, or fixed 84-character cap.
- No change to translation, dubbing, or subtitle rendering layout.
