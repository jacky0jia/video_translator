# Dubbing follow-ups

## D-01 — Reuse unchanged speech after approved translation compression

Status: deferred; user approved recording on 2026-10-05. Do not implement as part of the progress-bar UI work.

Current behavior: compression rewrites only the selected overlong translation rows and preserves all other text, but the next dubbing run synthesizes every utterance again. The full track and video are then rebuilt.

Desired behavior: synthesize only utterances whose spoken text or synthesis inputs changed; reuse verified raw speech for unchanged utterances. Recalculate the complete timeline using the selected uniform tempo and rebuild the final audio/video. A cache key must cover at least utterance text, voice, provider/model, synthesis speed, language, and relevant synthesis settings. Reject stale or missing audio rather than silently reusing it. Preserve the approved translation and keep subtitles and speech consistent.

Acceptance: tests cover selective synthesis, cache invalidation, missing/corrupt cache fallback, unchanged-translation preservation, and full-timeline recomputation; validate with a real compression-required dubbing task. Apply to both editions where the approval-based compression workflow exists.
