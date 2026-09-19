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

    def test_settings_separates_translation_diagnostics_from_qwen_runtime(self):
        root = Path(__file__).resolve().parents[1]
        modal = (root / "app/frontend/src/components/SettingsModal.jsx").read_text(encoding="utf-8")
        translations = (root / "app/frontend/src/i18n/translations.js").read_text(encoding="utf-8")

        self.assertNotIn("lmStudioVoiceCatalog", modal)
        self.assertNotIn("lmStudioTtsCapability", modal)
        self.assertIn("section.id === 'translation'", modal)
        self.assertIn("setLmStudioRefreshToken(token => token + 1)", modal)
        self.assertIn("refreshLmStudioDiagnostics", modal)
        self.assertIn('aria-busy={fetchingModels}', modal)
        self.assertIn('aria-live="polite"', modal)
        self.assertIn("setLmStudioRuntimeReady(null)", modal)
        self.assertIn("lmStudioStatusError", modal)
        self.assertIn("lmStudioDiagnosticsUnavailable", modal)
        self.assertIn("data.healthy === false", modal)
        self.assertIn("data.runtime_ready ?? null", modal)
        self.assertEqual(translations.count("refreshLmStudioDiagnostics:"), 2)
        self.assertEqual(translations.count("lmStudioDiagnosticsUnavailable:"), 2)
        self.assertIn("fetch('/api/qwen-tts/install'", modal)
        self.assertIn("qwenInstall.llama_archive", modal)
        self.assertIn("qwenInstall.cuda_archive", modal)
        self.assertIn("qwenInstall.model_directory", modal)
        self.assertIn("aria-busy={qwenInstalling}", modal)
        self.assertIn("qwenInstallResult?.installed", modal)
        self.assertIn("fetch('/api/qwen-tts/preflight'", modal)
        self.assertIn("fetch('/api/qwen-tts/install-status'", modal)
        self.assertIn("qwenPreflight?.ready", modal)
        self.assertIn("qwenInstallStatus?.installed", modal)
        self.assertIn("qwenInstallStatus?.managed", modal)
        self.assertIn("qwenRepairOpen", modal)
        self.assertIn("qwenPreflight.required_bytes", modal)
        self.assertIn('<option value="vulkan">AMD Vulkan</option>', modal)
        self.assertIn("qwenInstall.device !== 'vulkan'", modal)
        self.assertNotIn("no extra runtime is required", translations)
        self.assertNotIn("也无需安装额外运行时", translations)
        for key in ("qwenPrivateRuntimeInstall", "qwenLegacyRuntimeDetected", "qwenRepairRuntime", "qwenLlamaArchive", "qwenVulkanArchive", "qwenCudaArchive", "qwenModelDirectory", "qwenInstallSucceeded", "qwenInstallFailed"):
            self.assertEqual(translations.count(f"{key}:"), 2)

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
