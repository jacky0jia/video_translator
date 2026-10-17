import shutil
import subprocess
from pathlib import Path

import pytest


def test_model_preparation_is_classified_by_its_actual_stage():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")
    script = r"""
import assert from 'node:assert/strict';
import {isStandaloneTranscriptionEvent, stageIdForEvent} from './src/hooks/taskProcessingState.js';
for (const status of ['switching_model', 'waiting_for_gpu']) {
  for (const gpu_stage of ['tts', 'translation', 'render']) {
    const event = {status, gpu_stage};
    assert.equal(isStandaloneTranscriptionEvent(event), false);
    assert.notEqual(stageIdForEvent(event), 'transcribe');
  }
  assert.equal(isStandaloneTranscriptionEvent({status, gpu_stage:'asr'}), true);
  assert.equal(isStandaloneTranscriptionEvent({status, gpu_stage:'asr', pipeline_status:'processing'}), false);
  assert.equal(isStandaloneTranscriptionEvent({status, pipeline_stage:'pipeline_transcribing'}), false);
}
assert.equal(isStandaloneTranscriptionEvent({status:'transcribing'}), true);
assert.equal(isStandaloneTranscriptionEvent({status:'pending'}), true);
assert.equal(isStandaloneTranscriptionEvent({status:'completed'}), false);
assert.equal(isStandaloneTranscriptionEvent(null), false);
"""
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--input-type=module", "-e", script], cwd=root / "app/frontend",
                   check=True, capture_output=True, text=True)
    hook = (root / "app/frontend/src/hooks/useTaskProcessing.js").read_text(encoding="utf-8")
    assert "if (isStandaloneTranscriptionEvent(event))" in hook
    assert "if (!isStandaloneTranscriptionEvent(task)) return" in hook
