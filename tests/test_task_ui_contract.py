import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core.history import HistoryManager


class TaskUiContractTests(unittest.TestCase):
    def test_switching_tasks_restores_persisted_processing_state(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is required")
        root = Path(__file__).resolve().parents[1]
        script = r"""
import assert from 'node:assert/strict';
import {
  deriveTaskProcessingState, retryPlanForFailure, standaloneTranscriptionCompletion,
  subtitleVisibility,
} from './src/hooks/taskProcessingState.js';
const t = key => key;

const active = deriveTaskProcessingState({
  task_id: 'active', status: 'switching_model', pipeline_status: 'processing',
  pipeline_stage: 'pipeline_dubbing', progress_percent: 75,
  message: 'switching model', dubbing_status: 'processing',
}, t);
assert.deepEqual(active, {
  running: true, status: 'processing', progress: 75,
  currentStage: 'dub', message: 'Creating dubbing',
});

const activeWithoutProgress = deriveTaskProcessingState({
  status: 'translating', pipeline_status: 'processing', progress_percent: null,
}, t);
assert.equal(activeWithoutProgress.running, true);
assert.equal(activeWithoutProgress.progress, 4);

const burning = deriveTaskProcessingState({
  status: 'transcribed', burn_status: 'processing', progress_percent: 88,
}, t);
assert.equal(burning.running, true);
assert.equal(burning.progress, 88);

const failed = deriveTaskProcessingState({
  status: 'failed', pipeline_status: 'failed', failed_stage: 'pipeline_dubbing',
  message: 'worker failed', progress_percent: 75,
}, t);
assert.equal(failed.status, 'failed');
assert.equal(failed.currentStage, 'dub');
assert.equal(failed.message, 'worker failed');

const completed = deriveTaskProcessingState({status: 'completed'}, t);
assert.deepEqual(completed, {
  running: false, status: 'completed', progress: 100,
  currentStage: '', message: 'allOutputsReady',
});

assert.deepEqual(
  retryPlanForFailure('pipeline_translating', 'Translation failed', 'dubbing'),
  {forceTranslate: true, forceDub: true, resume: true},
);
assert.deepEqual(
  retryPlanForFailure('pipeline_dubbing', 'llama-tts synthesis timed out after 180s', 'dubbing'),
  {forceTranslate: false, forceDub: true, resume: true},
);
assert.deepEqual(
  retryPlanForFailure('pipeline_dubbing', '自然配音内容过长，即使加速仍超出视频。', 'dubbing'),
  {forceTranslate: true, forceDub: true, resume: true},
);
assert.deepEqual(standaloneTranscriptionCompletion({status:'transcribed'}, t), {
  running:false, status:'ready', progress:0, currentStage:'', message:'readyToProcess',
});
assert.equal(standaloneTranscriptionCompletion({status:'transcribing'}, t), null);
assert.deepEqual(subtitleVisibility('translated', true, true), {
  show_source:false, show_target:true,
});
assert.deepEqual(subtitleVisibility('bilingual', true, true), {
  show_source:true, show_target:true,
});
assert.deepEqual(subtitleVisibility('bilingual', true, true, false), {
  show_source:false, show_target:false,
});
"""
        subprocess.run(
            [node, "--input-type=module", "-e", script],
            cwd=root / "app" / "frontend",
            check=True,
            capture_output=True,
            text=True,
        )

    def test_settings_groups_provider_specific_options_and_keeps_qwen_checks(self):
        root = Path(__file__).resolve().parents[1]
        modal = (root / "app/frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
        layout = (root / "app/frontend/src/components/settings/SettingsLayout.jsx").read_text(encoding="utf-8")
        translations = (root / "app/frontend/src/i18n/translations.js").read_text(encoding="utf-8")

        for category in ("transcription", "translation", "dubbing", "video", "appearance"):
            self.assertIn(f"['{category}'", layout)
        self.assertNotIn("setup overview", layout.lower())
        self.assertNotIn("AboutSection", modal)
        self.assertIn("config.TTS_MODE === 'qwen'", layout)
        self.assertIn("config.TTS_MODE === 'kokoro'", layout)
        self.assertIn("config.TTS_MODE === 'edge'", layout)
        self.assertIn("QWEN_AUTO_CPU_FALLBACK", layout)
        self.assertIn("settingsKokoroVoicesHint", layout)
        self.assertIn("settingsQwenSourcePaths", layout)
        self.assertIn("qwenPreflight?.ready", layout)
        self.assertIn("fetch('/api/qwen-tts/preflight'", modal)
        self.assertIn("fetch('/api/qwen-tts/install'", modal)
        self.assertIn("fetch('/api/qwen-tts/install-status'", modal)
        self.assertIn("setLmStudioRefreshToken(token => token + 1)", layout)
        self.assertIn("setLmStudioRuntimeReady(null)", modal)
        self.assertIn("data.healthy === false", modal)
        self.assertIn("data.runtime_ready ?? null", modal)
        self.assertIn("'TTS_DEFAULT_VOICE', 'TTS_SPEED'", modal)
        self.assertIn("preset voice data", translations)
        self.assertIn("不是克隆声音的样本", translations)

    def test_upload_language_and_process_controls_follow_tts_capabilities(self):
        root = Path(__file__).resolve().parents[1]
        upload = (root / "app/frontend/src/components/UploadForm.jsx").read_text(encoding="utf-8")
        processing = (root / "app/frontend/src/hooks/useTaskProcessing.js").read_text(encoding="utf-8")
        panel = (root / "app/frontend/src/components/ProcessPanel.jsx").read_text(encoding="utf-8")
        sections = (root / "app/frontend/src/components/SettingsSections.jsx").read_text(encoding="utf-8")
        style_controls = (root / "app/frontend/src/components/SubtitleStyleControls.jsx").read_text(encoding="utf-8")

        self.assertIn("data.supported_languages", upload)
        self.assertIn("window.addEventListener('settings-changed', loadLanguages)", upload)
        self.assertNotIn('<option value="Korean">', upload)
        self.assertIn("setTtsMode(data.tts_mode", processing)
        self.assertIn("standaloneTranscriptionCompletion({ status: task?.status }, t)", processing)
        self.assertIn("running && route === 'dubbing' ? task?.task_id : null", processing)
        self.assertIn("cache: 'no-store'", processing)
        self.assertIn("controller?.abort()", processing)
        self.assertIn("setVoices([])", processing)
        self.assertIn("cache: 'no-store'", upload)
        self.assertIn("controller?.abort()", upload)
        self.assertIn("if (ttsMode === 'qwen') setSpeed(1)", processing)
        self.assertIn("onlineLanguages.includes('ko') ? 'ko-KR-SunHiNeural' : ''", processing)
        self.assertIn("onlineLanguages.includes(languageCode(targetLang))", panel)
        self.assertIn("route === 'dubbing' && !voice", panel)
        self.assertNotIn("Jacky Jia", sections)
        self.assertIn("Video Translator", sections)
        self.assertIn("sourceAndTargetFont", style_controls)

    def test_create_task_persists_target_language_and_output_map(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryManager(Path(temp_dir) / "history.json")
            task_id = history.create_task(
                "example.mp4",
                "upload",
                str(Path(temp_dir) / "example.mp4"),
                target_lang="Japanese",
            )

            task = history.get_task(task_id)

            self.assertEqual(task["target_lang"], "Japanese")
            self.assertEqual(task["subtitle_outputs"], {})

    def test_legacy_create_task_call_remains_compatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryManager(Path(temp_dir) / "history.json")
            task_id = history.create_task("legacy.mp4", "upload")

            task = history.get_task(task_id)

            self.assertIsNone(task["target_lang"])
            self.assertEqual(task["subtitle_outputs"], {})


if __name__ == "__main__":
    unittest.main()
