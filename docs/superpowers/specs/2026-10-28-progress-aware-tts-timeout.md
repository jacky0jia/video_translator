# Progress-Aware Qwen TTS Timeout Design

## Problem

Each `llama-tts` utterance currently has a fixed 180-second wall-clock
deadline. On slower devices or unusually expensive utterances, a healthy
process can still be generating frames when that deadline expires. The worker
then terminates the process and reports a synthesis failure even though forward
progress was being made.

Historical target-machine logs prove that individual attempts reached the
180-second limit, but predate the numeric TTS diagnostics and therefore cannot
show whether those attempts were slow or stalled. Current `llama-tts` output
periodically reports increasing generated-frame counts, which the existing
privacy-safe diagnostics already parse.

## Chosen behavior

Treat 180 seconds as a stall timeout rather than the total request lifetime.
The worker records the most recent generated-frame count and refreshes the
stall deadline only when that count increases. Startup and model loading are
covered by the initial 180-second window.

Also enforce a 600-second absolute deadline measured from process start. A
process that continues producing progress indefinitely is still terminated at
that limit. Cancellation remains immediate, and either timeout path must use
the existing terminate-and-reap cleanup.

Timeout errors must distinguish the two cases without including source text,
voice samples, paths, or raw subprocess output:

- no progress for 180 seconds;
- absolute runtime exceeded 600 seconds.

Callers that explicitly supply a per-request timeout retain that value as the
stall timeout. The absolute ceiling remains 600 seconds unless the worker is
constructed with an explicit ceiling for controlled tests or packaging needs.
The public default stays compatible at 180 seconds, but its meaning becomes
"maximum time without progress."

## Progress source and safety

Use only the numeric snapshot maintained by `TTSDiagnostics`; do not add a
second reader for subprocess output. Progress refresh requires a strictly
larger generated-frame count, so duplicate log lines and unrelated output
cannot keep a stuck process alive. If a runtime emits no parseable progress,
the behavior safely falls back to the existing 180-second timeout.

## Verification

- Prove a request can run beyond 180 seconds when generated-frame counts keep
  increasing.
- Prove duplicate or unchanged frame counts do not refresh the stall deadline.
- Prove 180 seconds without progress terminates and reaps the process.
- Prove continued progress cannot exceed the 600-second absolute ceiling.
- Prove explicit per-request stall timeouts, cancellation, privacy-safe
  diagnostics, and successful synthesis remain compatible.
- Update the acceptance issue record to describe the mitigation while keeping
  the historical timeout evidence intact.
- Run the complete Python suite and frontend tests/build before delivery.

## Implementation status

The historical target-machine failures at 180 seconds remain valid evidence:
those attempts reached the former fixed deadline, while their logs cannot
establish whether generation was slow or stalled. The mitigation is now
implemented: requests time out after 180 seconds without generated-frame
progress and are also subject to a 600-second absolute runtime ceiling. The
absolute ceiling applies even while frame counts continue to increase.
