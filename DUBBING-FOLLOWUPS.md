# Dubbing follow-ups

## D-01 — Reuse unchanged speech after approved translation compression

Status: implemented in source on 2026-10-05 after user authorization; real-engine user acceptance pending.

Current behavior: compression rewrites only the selected overlong translation rows and preserves all other text. Dubbing reuses verified raw speech from a task-owned cache and synthesizes only changed utterances. The full timeline, audio track, and video are rebuilt. Continuous subtitle fragments are grouped into sentences; editing a fragment may require regenerating its whole sentence.

Desired behavior: synthesize only utterances whose spoken text or synthesis inputs changed; reuse verified raw speech for unchanged utterances. Recalculate the complete timeline using the selected uniform tempo and rebuild the final audio/video. A cache key must cover at least utterance text, voice, provider/model, synthesis speed, language, and relevant synthesis settings. Reject stale or missing audio rather than silently reusing it. Preserve the approved translation and keep subtitles and speech consistent.

Validation: regression tests cover selective synthesis, configuration/asset invalidation, missing/corrupt cache fallback, cancellation, fallback voice identity, and unchanged translations. A deterministic compression-required fixture uses real FFmpeg to verify preserved cache across temporary-work cleanup, selective resynthesis, matching subtitles, and final track/video regeneration. LLM/TTS are isolated in that fixture; it does not replace user acceptance with a real voice engine. The cache is under `TEMP_DIR/<task_id>/speech-cache` and is removed by existing task deletion/retranscription cleanup. Older tasks with no cache synthesize all utterances on their first new dubbing run.

Validation checkpoint: public cache/pipeline targeted tests passed (24 tests) before the user requested postponing further testing because the GPU was occupied. Final full-suite regression, paid-edition regression, and real-engine acceptance remain pending; no further model-backed tests should run until the user resumes testing. Both editions have the cache and completed-track configuration guard; the paid edition's older pipeline does not yet have the public edition's approval-based compression endpoint.

User acceptance: start with a newly dubbed task to populate the cache, edit one subtitle sentence or approve a required compression, then recreate dubbing. Check `dubbing_synthesis` in task history or the log summary for reused/synthesized counts; listen across changed sentence boundaries and confirm subtitles match. Change voice or synthesis speed to confirm incompatible speech is regenerated.
