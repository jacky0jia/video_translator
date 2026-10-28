# Progress-Aware Qwen TTS Timeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a healthy `llama-tts` utterance to continue past 180 seconds while terminating stalled work after 180 seconds without frame progress and all work after 600 seconds.

**Architecture:** Reuse the numeric frame count already parsed by `TTSDiagnostics` as the sole progress signal. The worker wait loop maintains independent stall and absolute deadlines, while the existing attempt owner remains responsible for cancellation, process termination, diagnostic draining, and cleanup.

**Tech Stack:** Python 3, `asyncio`, `unittest`, pytest, existing `TTSDiagnostics`

**Spec:** `docs/superpowers/specs/2026-10-28-progress-aware-tts-timeout.md`

## Global Constraints

- The default stall timeout is exactly 180 seconds.
- The default absolute request ceiling is exactly 600 seconds.
- Only a strictly increasing parsed generated-frame count refreshes the stall deadline.
- A runtime with no parseable frame progress retains the existing bounded timeout behavior.
- Cancellation remains immediate and both timeout paths terminate and reap the subprocess.
- Timeout messages and diagnostics must not contain source text, sample paths, or raw subprocess output.
- Every implementation change includes tests, all validation passes, and each completed change is committed.

## Review Focus

- A runtime that emits no frame lines must fail through the stall timeout and still be reaped.
- Duplicate or decreasing frame counts must not keep a stalled child alive.
- Continuous increasing progress must not bypass the 600-second absolute ceiling.
- Cancellation arriving near either deadline must retain cancellation precedence.
- An explicitly supplied per-request timeout must change only the stall window, not the absolute ceiling.

---

### Task 1: Progress-aware worker deadlines

**Files:**
- Modify: `app/workers/llama_tts_worker.py:159-410`
- Test: `tests/test_llama_tts_worker.py:53-400`

**Interfaces:**
- Consumes: `TTSDiagnostics.metrics["frames"]`, an optional monotonically increasing integer populated by the existing output reader.
- Produces: `LlamaTTSWorkerClient.__init__(..., request_timeout: float = 180, absolute_timeout: float = 600, ...)` and `_wait(process, *, stall_timeout: float, absolute_timeout: float, should_cancel: Callable[[], bool] | None, diagnostic: TTSDiagnostics) -> None`.

- [ ] **Step 1: Write failing wait-loop tests**

Add asynchronous tests proving that increasing frame counts allow `_wait` to outlive the stall window, unchanged/decreasing counts produce a `no progress` timeout, continuous progress produces an `absolute runtime` timeout, and cancellation wins when cancellation and a deadline are both eligible.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest tests/test_llama_tts_worker.py -q`

Expected: FAIL because the constructor and `_wait` do not yet support an independent absolute timeout or diagnostic progress.

- [ ] **Step 3: Implement the two-deadline wait loop**

Add the 600-second constructor setting, pass the active diagnostic into `_wait`, and track `last_frames`, `stall_deadline`, and `absolute_deadline` from `time.monotonic()`. Refresh only `stall_deadline` when a parsed integer frame count is strictly greater than `last_frames`. Check cancellation first, then the absolute deadline, then the stall deadline. Emit distinct privacy-safe timeout messages.

- [ ] **Step 4: Extend full-attempt cleanup and compatibility tests**

Update the existing timeout/reap test to assert the `no progress` reason. Add tests proving an explicit `synthesize(timeout=...)` value is used as the stall timeout while the configured absolute timeout remains in force, and that a successful ordinary synthesis is unchanged.

- [ ] **Step 5: Run worker and diagnostics tests**

Run: `python -m pytest tests/test_llama_tts_worker.py tests/test_tts_diagnostics.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the worker change**

```bash
git add app/workers/llama_tts_worker.py tests/test_llama_tts_worker.py
git commit -m "fix: keep progressing TTS requests alive"
```

### Task 2: Acceptance record and complete verification

**Files:**
- Modify: `specs/acceptance-issues-20261017.md:42-52`
- Modify: `tests/test_acceptance_issue_register.py:17-23`

**Interfaces:**
- Consumes: the timeout behavior implemented by Task 1.
- Produces: a durable acceptance record that preserves the historical 180-second failures and records the new 180-second stall/600-second absolute policy.

- [ ] **Step 1: Write the failing acceptance-register assertion**

Require the issue register to state that the historical failures remain valid evidence and that mitigation now uses a 180-second no-progress timeout plus a 600-second absolute ceiling.

- [ ] **Step 2: Run the acceptance-register test and verify it fails**

Run: `python -m pytest tests/test_acceptance_issue_register.py -q`

Expected: FAIL because the mitigation is not yet recorded.

- [ ] **Step 3: Update the acceptance issue record**

Replace only the obsolete statement that timeout extension is deferred; preserve the timestamps and conclusion that the first two attempts genuinely failed.

- [ ] **Step 4: Run the acceptance and focused worker tests**

Run: `python -m pytest tests/test_acceptance_issue_register.py tests/test_llama_tts_worker.py tests/test_tts_diagnostics.py -q`

Expected: all tests pass.

- [ ] **Step 5: Run complete project verification**

Run:

```powershell
python -m pytest -q
$nodeTests = (Get-ChildItem tests\*.test.cjs,tests\*.test.mjs).FullName
node --test $nodeTests
npm --prefix app/frontend run build
```

Expected: all Python tests pass (apart from documented skips), all frontend tests pass, and the production build exits successfully.

- [ ] **Step 6: Commit the acceptance record**

```bash
git add specs/acceptance-issues-20261017.md tests/test_acceptance_issue_register.py
git commit -m "docs: record progress-aware TTS timeout mitigation"
```

