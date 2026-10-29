# H05 ASR Context and Subtitle Segmentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the misleading short ASR-window setting, always give local faster-whisper 30 seconds of context with word timestamps, and split its output into ordinary subtitles using the user-configured subtitle segment duration.

**Architecture:** Keep recognition policy and presentation policy separate. A shared local-ASR module owns the fixed 30-second context constant and a pure conversion function from faster-whisper segments to application `TranscriptionSegment` rows; both the in-process service and worker-process path use it, while the remote API path remains unchanged.

**Tech Stack:** Python 3.11, faster-whisper 1.2.1-compatible segment objects, Pydantic, YAML configuration migrations, React schema-driven settings, Node test runner, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-10-29-asr-context-and-subtitle-segmentation-design.md`

## Global Constraints

- Local faster-whisper recognition context is fixed at exactly 30 seconds and always requests `word_timestamps=True`.
- The setting key is `SUBTITLE_SEGMENT_SECONDS`, displayed as **Subtitle segment duration (seconds)** / **字幕分片时间（秒）**, default 8, range 1 through 30.
- Version-1 `ASR_CHUNK_LENGTH` migrates to the new key; 6 and 8 must remain 6 and 8, the obsolete key must not be persisted, and migration must be idempotent.
- Remote OpenAI-compatible ASR behavior and payloads remain unchanged.
- No phrase blacklist, automatic text deletion, global `hallucination_silence_threshold`, extra VAD pass, rolling overlap, or fixed character ceiling.
- Preserve recognized word order, text, and monotonic timestamps; malformed or missing word timestamps keep the original upstream segment unchanged.
- Every implementation task starts with a failing test, ends with passing relevant tests, and creates its own Git commit.
- Do not upgrade faster-whisper or CTranslate2 in this branch.

## Review Focus

- A config containing both old and new keys must keep the explicit new value and remove the old key; Task 1 adds this migration test.
- Legacy non-integer, non-positive, or greater-than-30 chunk values must not make startup fail: use 8 for invalid/non-positive values and clamp values above 30; Task 1 adds boundary tests.
- Missing, non-finite, reversed, or non-monotonic word timestamps must return the untouched upstream segment; Task 2 tests each invalid class.
- Punctuation-free speech, long gaps, exact-duration boundaries, and a single word longer than the duration must always make progress without empty rows or dropped words; Task 2 covers these cases.
- In-process and worker local paths must emit equivalent split rows while remote rows remain unsplit; Tasks 3 and 4 add parity and remote-boundary tests.

---

## Preflight: Confirm the branch baseline

- [ ] Run the complete Python suite before edits.

Run: `python -m pytest -q -p no:cacheprovider`

Expected: PASS. If it fails, stop and record the pre-existing failures before changing code.

- [ ] Run all Node contract tests and the production frontend build before edits.

Run: `node --test tests/*.test.mjs`

Run: `npm --prefix app/frontend run build`

Expected: all Node tests PASS and Vite build exits 0.

### Task 1: Replace the persisted ASR-window setting

**Files:**
- Modify: `app/core/config_migrations.py`
- Modify: `app/core/config.py`
- Modify: `app/core/settings_schema.py`
- Modify: `config.example.yaml`
- Modify: `app/frontend/src/i18n/translations.js`
- Modify: `tests/test_settings_schema.py`
- Create: `tests/asr-segment-setting.test.mjs`

**Interfaces:**
- Consumes: versioned flat YAML handled by `migrate_config(raw: Mapping[str, Any]) -> dict[str, Any]`.
- Produces: `settings.SUBTITLE_SEGMENT_SECONDS: int`; schema key `SUBTITLE_SEGMENT_SECONDS`; config version 2; UI schema version 2.

- [ ] **Step 1: Write failing migration and schema tests**

Add tests named `test_v1_asr_chunk_migrates_to_subtitle_duration_idempotently`, `test_explicit_new_subtitle_duration_wins_over_legacy_key`, and `test_legacy_asr_chunk_outliers_use_safe_supported_values`. Assert version 2, 6/8 preservation, removal of `ASR_CHUNK_LENGTH`, new-key precedence, invalid/non-positive fallback 8, and values above 30 clamped to 30. Update the registry test to expect schema version 2, default 8, min 1, max 30, and visibility only when `ASR_MODE == "local"`.

- [ ] **Step 2: Write the failing localized-copy contract test**

In `tests/asr-segment-setting.test.mjs`, assert `translations.en.subtitleSegmentSeconds === 'Subtitle segment duration (seconds)'` and `translations.zh.subtitleSegmentSeconds === '字幕分片时间（秒）'`, and assert the obsolete `asrChunkLength` key is absent from both dictionaries.

- [ ] **Step 3: Run the new tests and verify they fail**

Run: `python -m pytest tests/test_settings_schema.py -q -p no:cacheprovider`

Run: `node --test tests/asr-segment-setting.test.mjs`

Expected: FAIL because config version 2 and the new setting/copy do not exist.

- [ ] **Step 4: Implement config migration version 2**

In `app/core/config_migrations.py`, advance `CURRENT_CONFIG_VERSION` to 2 and add the sequential version-1-to-2 migration. If `SUBTITLE_SEGMENT_SECONDS` is absent, derive it from `ASR_CHUNK_LENGTH`: integer 1–30 unchanged, greater than 30 becomes 30, invalid or non-positive becomes 8. If the new key already exists, preserve it. Always remove `ASR_CHUNK_LENGTH` and set version 2.

- [ ] **Step 5: Replace the runtime and public setting**

In `Settings`, replace `ASR_CHUNK_LENGTH` with `SUBTITLE_SEGMENT_SECONDS = 8` in defaults, integer normalization, `_known_config_data`, and `to_public_dict`. In `settings_schema.py`, replace the old definition with the new integer definition using order 40, default 8, min 1, max 30, label `subtitleSegmentSeconds`, advanced level, next-task apply policy, and `_when("ASR_MODE", "local")`; advance `SCHEMA_VERSION` to 2. Replace the example YAML key/value with `SUBTITLE_SEGMENT_SECONDS: 8` and replace both translation keys.

- [ ] **Step 6: Run configuration and copy tests**

Run: `python -m pytest tests/test_settings_schema.py -q -p no:cacheprovider`

Run: `node --test tests/asr-segment-setting.test.mjs`

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- app/core/config_migrations.py app/core/config.py app/core/settings_schema.py config.example.yaml app/frontend/src/i18n/translations.js tests/test_settings_schema.py tests/asr-segment-setting.test.mjs
git commit -m "feat: replace ASR chunk setting with subtitle duration"
```

### Task 2: Add deterministic word-timestamp subtitle segmentation

**Files:**
- Create: `app/services/asr_segments.py`
- Create: `tests/test_asr_segments.py`

**Interfaces:**
- Consumes: iterable faster-whisper-like segment objects with `start`, `end`, `text`, `avg_logprob`, and optional `words`; word objects expose `start`, `end`, and `word`.
- Produces: `LOCAL_ASR_CONTEXT_SECONDS: Final[int] = 30` and `split_faster_whisper_segments(segments: Iterable[Any], max_duration_seconds: float) -> list[TranscriptionSegment]`.

- [ ] **Step 1: Write failing short/exact-duration preservation tests**

Assert a segment shorter than 8 seconds and one exactly 8 seconds retain their original start, end, text, and confidence as one row, even when words exist.

- [ ] **Step 2: Write failing punctuation and word-fallback tests**

Assert a long English segment chooses the latest `.?!,;:` boundary at or before 8 seconds, a Chinese segment recognizes `。！？；：，`, and punctuation-free input falls back to the latest word end at or before the limit. Assert concatenated fragment word text equals the original word sequence and timestamps are monotonic and non-overlapping.

- [ ] **Step 3: Write failing malformed/boundary tests**

Cover missing words, `None`/NaN/Infinity timestamps, reversed timestamps, non-monotonic words, blank reconstructed text, long gaps, and a single word whose duration exceeds the setting. Invalid word metadata returns the untouched upstream segment; valid edge cases produce non-empty rows and consume every word exactly once.

- [ ] **Step 4: Run the helper tests and verify they fail**

Run: `python -m pytest tests/test_asr_segments.py -q -p no:cacheprovider`

Expected: FAIL because `app.services.asr_segments` does not exist.

- [ ] **Step 5: Implement the pure splitter**

Create the constant and function with the signatures above. Keep upstream segments whose duration is within the limit. For longer valid word-timestamp segments, repeatedly select the latest punctuation-ending word within `fragment_start + max_duration_seconds`; otherwise select the latest word ending within the limit; if none fits, consume one word. Reconstruct text by joining the selected `word` strings and trimming only outer whitespace. If validation fails at any point, return the original segment as one `TranscriptionSegment` instead of estimating or deleting text.

- [ ] **Step 6: Run helper tests**

Run: `python -m pytest tests/test_asr_segments.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```powershell
git add -- app/services/asr_segments.py tests/test_asr_segments.py
git commit -m "feat: split ASR output by word timestamps"
```

### Task 3: Apply the fixed context and splitter in the in-process path

**Files:**
- Modify: `app/services/asr_quality.py`
- Modify: `app/services/asr_service.py`
- Modify: `tests/test_asr_quality.py`
- Create: `tests/test_asr_service_segments.py`

**Interfaces:**
- Consumes: `LOCAL_ASR_CONTEXT_SECONDS` and `split_faster_whisper_segments` from Task 2; `settings.SUBTITLE_SEGMENT_SECONDS` from Task 1.
- Produces: in-process local ASR rows already segmented as application `TranscriptionSegment` objects; unchanged remote API rows.

- [ ] **Step 1: Write failing local option and segmentation tests**

Mock a local model returning a greater-than-8-second segment with word timestamps. Assert the model call includes `chunk_length=30` and `word_timestamps=True`, omits `vad_filter` and `hallucination_silence_threshold`, and the returned `TranscriptionResult` contains the expected split rows. Update retry tests to prove recovery retains `word_timestamps=True` and the fixed 30-second context.

- [ ] **Step 2: Write a failing remote-boundary test**

Use a fake async HTTP client returning one 12-second verbose-JSON segment while `SUBTITLE_SEGMENT_SECONDS=8`. Assert the remote request remains OpenAI-compatible and the result remains one unchanged row, proving the local splitter is not applied to remote responses.

- [ ] **Step 3: Run the service tests and verify they fail**

Run: `python -m pytest tests/test_asr_quality.py tests/test_asr_service_segments.py -q -p no:cacheprovider`

Expected: FAIL because local calls still use the old setting and do not request words or split output.

- [ ] **Step 4: Wire the in-process path**

Import the Task 2 interfaces in `asr_service.py`. Call `transcribe_checked` with fixed context 30 and `word_timestamps=True`, then replace the manual raw-segment loop with `split_faster_whisper_segments(segments, settings.SUBTITLE_SEGMENT_SECONDS)`. In `asr_quality.py`, use the shared context constant for retry instead of a second literal; preserve all caller options so word timestamps survive retry.

- [ ] **Step 5: Run the service tests**

Run: `python -m pytest tests/test_asr_quality.py tests/test_asr_service_segments.py -q -p no:cacheprovider`

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```powershell
git add -- app/services/asr_quality.py app/services/asr_service.py tests/test_asr_quality.py tests/test_asr_service_segments.py
git commit -m "feat: use fixed ASR context in local service"
```

### Task 4: Keep the worker path equivalent

**Files:**
- Modify: `app/workers/asr_worker.py`
- Modify: `app/services/asr_service.py`
- Modify: `tests/test_asr_quality.py`
- Modify: `tests/test_provider_boundaries.py`

**Interfaces:**
- Consumes: Task 1 setting and Task 2 splitter/constant.
- Produces: `_worker_main(connection, model_args, transcribe_args, subtitle_segment_seconds)` and `ASRWorkerClient.start(*, model_args: dict, transcribe_args: dict, subtitle_segment_seconds: float) -> None`.

- [ ] **Step 1: Write failing worker option/serialization tests**

Extend the direct `_worker_main` test with a long raw segment and words. Assert `transcribe_checked` receives fixed `chunk_length=30` and `word_timestamps=True`; assert the pipe result contains the same split dictionaries as the in-process test, including confidence and timestamps.

- [ ] **Step 2: Write a failing stage-start contract test**

Update the fake worker in `test_asr_worker_is_shared_for_stage_and_closed` to capture `start` keyword arguments. Assert `transcribe_args` contains beam size, task, context 30, and word timestamps, and `subtitle_segment_seconds` equals the current setting. Assert neither VAD nor hallucination-threshold options are present.

- [ ] **Step 3: Run worker tests and verify they fail**

Run: `python -m pytest tests/test_asr_quality.py tests/test_provider_boundaries.py -q -p no:cacheprovider`

Expected: FAIL because the worker still accepts three process arguments and serializes unsplit raw segments.

- [ ] **Step 4: Implement worker parity**

Pass `settings.SUBTITLE_SEGMENT_SECONDS` separately when starting the worker. Extend `ASRWorkerClient.start` and its process target arguments accordingly. In `_worker_main`, run the Task 2 splitter before sending results and serialize each returned `TranscriptionSegment` with `model_dump(mode="json")`. Keep faster-whisper options separate from application segmentation configuration.

- [ ] **Step 5: Run worker and service regression tests**

Run: `python -m pytest tests/test_asr_quality.py tests/test_asr_service_segments.py tests/test_provider_boundaries.py -q -p no:cacheprovider`

Expected: PASS, with in-process and worker expected rows identical.

- [ ] **Step 6: Commit Task 4**

```powershell
git add -- app/workers/asr_worker.py app/services/asr_service.py tests/test_asr_quality.py tests/test_provider_boundaries.py
git commit -m "feat: segment ASR output in worker process"
```

### Task 5: Document and verify H05 end to end

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: completed Tasks 1–4.
- Produces: user-facing documentation and recorded validation evidence; no new runtime interface.

- [ ] **Step 1: Update user documentation**

Add `SUBTITLE_SEGMENT_SECONDS` to both settings lists, describe it as display segmentation for local ASR rather than recognition context, and add an Unreleased changelog entry stating that local recognition now uses 30-second context plus word-timestamp subtitle segmentation. Do not describe the VLC draft as a shipped dependency.

- [ ] **Step 2: Run targeted H05 tests**

Run: `python -m pytest tests/test_settings_schema.py tests/test_asr_segments.py tests/test_asr_quality.py tests/test_asr_service_segments.py tests/test_provider_boundaries.py -q -p no:cacheprovider`

Run: `node --test tests/asr-segment-setting.test.mjs`

Expected: PASS.

- [ ] **Step 3: Run all automated regression checks**

Run: `python -m pytest -q -p no:cacheprovider`

Run: `node --test tests/*.test.mjs`

Run: `npm --prefix app/frontend run build`

Expected: all tests PASS and Vite build exits 0.

- [ ] **Step 4: Rerun the isolated 8-minute-30-second sample**

Run from the repository root:

```powershell
& 'I:\AI Coding\public tools\VideoTranslator-supporter-52e9d23-win-x64\runtime\python.exe' `
  'I:\AI Coding\codex\subtitle_translator\.codex-test-runs\ghost-text-20261017\compare_cpu.py' `
  --variant window30_words `
  --source 'I:\AI Coding\public tools\VideoTranslator-supporter-52e9d23-win-x64\app\uploads\a0c9983a7ca34d9db8772bdc217b50e0.mkv' `
  --model 'I:\AI Coding\public tools\VideoTranslator-supporter-52e9d23-win-x64\models\faster-whisper-small' `
  --seconds 510.374625 `
  --output 'I:\AI Coding\codex\subtitle_translator\.codex-test-runs\ghost-text-20261017\post-h05-window30-words.json'
```

Expected: faster-whisper 1.2.1/CTranslate2 4.8.1 run completes; `Thanks for watching.` occurs zero times; there are no overlapping rows or consecutive identical rows; real countdown/dialogue remains. Record elapsed time, row count, phrase count, and overlap count in the handoff checkpoint. If the source/model path or SHA-256 differs from the prior report (`994db05695f9395f2a45456ec88d298fc90a56c6c76d251af60118311277edab` for decoded audio), stop and report that the comparison is not like-for-like.

- [ ] **Step 5: Verify the final diff and commit documentation**

Run: `git diff --check`

Expected: no output.

```powershell
git add -- README.md README.zh-CN.md CHANGELOG.md
git commit -m "docs: explain subtitle segment duration"
```

- [ ] **Step 6: Perform final branch review**

Verify `rg -n "ASR_CHUNK_LENGTH" app tests config.example.yaml README.md README.zh-CN.md` returns no matches, `git status --short` is empty, and every Task 1–5 commit is present. Then request code review before push or merge; do not merge this branch automatically.
