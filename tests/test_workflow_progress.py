import shutil
import subprocess
import unittest
from pathlib import Path


class WorkflowProgressTests(unittest.TestCase):
    def test_completion_is_scoped_to_required_stages_without_mutating_task_state(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required")
        script = r"""
import assert from 'node:assert/strict';
import {completedStageIds, deriveTaskProcessingState, selectedWorkflowState} from './src/hooks/taskProcessingState.js';
const task = {status:'completed', target_lang:'Chinese', transcription_path:'source.json',
  translations:{Chinese:'translation.json'}};
const saved = deriveTaskProcessingState(task);
const done = completedStageIds(task, 'Chinese');
const subtitles = ['transcribe', 'translate'];
const dubbing = [...subtitles, 'dub', 'render'];
assert.deepEqual(done, subtitles);
assert.equal(selectedWorkflowState(saved, done, subtitles).status, 'completed');
const pending = selectedWorkflowState(saved, done, dubbing);
assert.equal(pending.status, 'ready');
assert.equal(pending.progress, 0);
assert.equal(pending.message, 'readyToProcess');
assert.equal(pending.currentStage, '');
assert.equal(saved.status, 'completed');
assert.equal(saved.progress, 100);
assert.equal(selectedWorkflowState(saved, done, subtitles).progress, 100);
assert.equal(selectedWorkflowState(saved, done, [...subtitles, 'render']).status, 'ready');
assert.equal(selectedWorkflowState(saved, done, [...subtitles, 'dub']).status, 'ready');
const fullyDone = {...task, dubbing_status:'completed', dubbing_target_lang:'Chinese',
  dubbing_audio_path:'dub.wav', dubbing_video_path:'dub.mp4', dubbing_end_note_enabled:true};
assert.equal(selectedWorkflowState(saved, completedStageIds(fullyDone, 'Chinese'), dubbing).status, 'completed');
assert.equal(selectedWorkflowState(saved, completedStageIds(fullyDone, 'Japanese'), dubbing).status, 'ready');
for (const status of ['failed', 'cancelled', 'awaiting_compression', 'processing']) {
  const state = {...saved, status, progress:42, currentStage:'dub', running:status==='processing'};
  assert.strictEqual(selectedWorkflowState(state, done, dubbing), state);
}
"""
        subprocess.run([node, "--input-type=module", "-e", script],
                       cwd=Path(__file__).resolve().parents[1] / "app/frontend",
                       check=True, capture_output=True, text=True)

    def test_stage_coloring_uses_actual_stage_completion(self):
        root = Path(__file__).resolve().parents[1] / "app/frontend/src"
        panel = (root / "components/ProcessPanel.jsx").read_text(encoding="utf-8")
        hook = (root / "hooks/useTaskProcessing.js").read_text(encoding="utf-8")
        self.assertIn("const done = completedStages.includes(value) && !active", panel)
        self.assertNotIn("completedStages.includes(value) || status === 'completed'", panel)
        self.assertNotIn("activeIndex > index", panel)
        self.assertIn("const displayState = selectedWorkflowState(", hook)
        self.assertIn("...displayState", hook)
