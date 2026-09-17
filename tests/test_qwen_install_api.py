import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.api.config import (
    QwenTTSInstallRequest,
    get_qwen_tts_install_status,
    get_qwen_tts_runtime_status,
    install_qwen_tts,
    preflight_qwen_tts,
)
from app.services.tts.qwen_installer import QwenTTSInstallError


class QwenTTSInstallAPITests(unittest.TestCase):
    def test_qwen_fallback_is_boolean_and_applies_next_task(self):
        from app.core.settings_schema import SETTINGS_BY_KEY, normalize_setting_value
        field = SETTINGS_BY_KEY["QWEN_AUTO_CPU_FALLBACK"]
        self.assertTrue(field.default)
        self.assertEqual(field.apply_policy, "next_task")
        self.assertFalse(normalize_setting_value(field, False))

    def test_runtime_status_is_local_and_returns_snapshot(self):
        with patch("app.services.tts.qwen._runtime_status", {"actual_device": "cpu", "state": "succeeded", "fallback_reason": "CUDA exit 1"}):
            result = asyncio.run(get_qwen_tts_runtime_status(self.request()))
            self.assertEqual(result["actual_device"], "cpu")
            self.assertEqual(result["fallback_reason"], "CUDA exit 1")
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(get_qwen_tts_runtime_status(self.request("192.0.2.10")))
            self.assertEqual(raised.exception.status_code, 403)

    def test_fallback_checkbox_renders_and_passes_boolean(self):
        import shutil
        import subprocess
        frontend = Path(__file__).resolve().parents[1] / "app" / "frontend"
        if not shutil.which("node") or not (frontend / "node_modules" / "esbuild").exists():
            self.skipTest("Frontend dependencies are required")
        script = r"""
const fs = require('fs');
const esbuild = require('esbuild');
const Module = require('module');
const path = require('path');
const filename = path.resolve('src/components/settings/SettingsField.jsx');
const code = esbuild.transformSync(fs.readFileSync(filename, 'utf8'), {loader:'jsx', format:'cjs', jsx:'automatic'}).code;
const mod = new Module(filename, module);
mod.filename = filename;
mod.paths = module.paths;
mod._compile(code, filename);
let change;
const tree = mod.exports.default({field:{key:'QWEN_AUTO_CPU_FALLBACK',value_type:'boolean',default:true}, config:{QWEN_AUTO_CPU_FALLBACK:false},onChange:(...args)=>change=args,t:x=>x});
const input = tree.props.children.find(x=>x && x.type==='input');
require('assert').strictEqual(input.props.type,'checkbox');
require('assert').strictEqual(input.props.checked,false);
input.props.onChange({target:{checked:true}});
require('assert').deepStrictEqual(change,['QWEN_AUTO_CPU_FALLBACK',true]);
"""
        subprocess.run([shutil.which("node"), "-e", script], cwd=frontend, check=True, capture_output=True)

    def test_runtime_status_messages_match_actual_device_and_escape_errors(self):
        import shutil
        import subprocess
        frontend = Path(__file__).resolve().parents[1] / "app" / "frontend"
        if not shutil.which("node") or not (frontend / "node_modules" / "esbuild").exists():
            self.skipTest("Frontend dependencies are required")
        script = r"""
const fs = require('fs'), path = require('path'), Module = require('module');
const esbuild = require('esbuild'), assert = require('assert');
const React = require('react'), {renderToStaticMarkup} = require('react-dom/server');
function load(file, loader) {
  const filename = path.resolve(file), mod = new Module(filename, module);
  mod.filename = filename; mod.paths = module.paths;
  mod._compile(esbuild.transformSync(fs.readFileSync(filename, 'utf8'), {loader, format:'cjs', jsx:'automatic'}).code, filename);
  return mod.exports;
}
const Component = load('src/components/settings/QwenRuntimeStatus.jsx', 'jsx').default;
const {translations} = load('src/i18n/translations.js', 'js');
for (const lang of ['en', 'zh']) {
  const t = key => { assert.ok(translations[lang][key], key); return translations[lang][key]; };
  const render = status => renderToStaticMarkup(React.createElement(Component, {status, t}));
  assert.equal(render(null), '');
  for (const [device, state, key] of [
    ['cuda', 'retrying', 'qwenCpuFallbackPending'],
    ['cuda', 'cancelled', 'qwenCpuFallbackCancelled'],
    ['cpu', 'running', 'qwenCpuFallbackWarning'],
    ['cpu', 'succeeded', 'qwenCpuFallbackWarning'],
    ['cpu', 'failed', 'qwenCpuFallbackWarning'],
    ['cpu', 'cancelled', 'qwenCpuFallbackWarning'],
  ]) {
    const html = render({actual_device:device, state, fallback_reason:'<script>error</script>'});
    assert.ok(html.includes(t(key)));
    if (device === 'cuda') assert.ok(!html.includes(t('qwenCpuFallbackWarning')));
    assert.ok(html.includes('role="alert"'));
    assert.ok(!html.includes('<script>'));
    assert.ok(html.includes('&lt;script&gt;'));
  }
  assert.ok(render({state:'failed', actual_device:'cuda'}).includes('role="alert"'));
  assert.ok(render({state:'idle'}).includes('role="status"'));
  assert.ok(render({state:'future-state'}).includes(t('qwenState_unknown')));
}
"""
        subprocess.run([shutil.which("node"), "-e", script], cwd=frontend, check=True, capture_output=True, timeout=30)

    def request(self, host="127.0.0.1"):
        return SimpleNamespace(client=SimpleNamespace(host=host))

    def payload(self, model_directory="C:/LM Studio/model"):
        return QwenTTSInstallRequest(
            llama_archive="C:/Downloads/llama.zip",
            cuda_archive="C:/Downloads/cuda.zip",
            model_directory=model_directory,
        )

    def vulkan_payload(self):
        return QwenTTSInstallRequest(
            llama_archive="C:/Downloads/llama-vulkan.zip",
            model_directory="D:/models/qwen",
            device="vulkan",
        )

    def test_remote_clients_cannot_trigger_local_file_installation(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(install_qwen_tts(self.request("192.0.2.10"), self.payload()))
        self.assertEqual(raised.exception.status_code, 403)

    def test_local_install_uses_only_pinned_model_names_and_app_destination(self):
        captured = {}

        def install(**kwargs):
            captured.update(kwargs)

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.api.config.settings.BASE_DIR", Path(directory) / "app"
        ), patch("app.api.config.assemble_qwen_tts_bundle", install), patch(
            "app.api.config.inspect_qwen_tts_installation",
            return_value={"installed": True, "managed": True, "device": "cuda"},
        ):
            result = asyncio.run(install_qwen_tts(self.request(), self.payload("D:/models/qwen")))
        self.assertTrue(result["installed"])
        self.assertEqual(captured["model_path"].name, "Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf")
        self.assertEqual(captured["mmproj_path"].name, "mmproj-Qwen3-TTS-12Hz-1.7B-Base-bf16.gguf")
        self.assertEqual(captured["destination"], Path(directory) / "models" / "qwen3-tts")
        self.assertEqual(captured["device"], "cuda")

    def test_preflight_returns_sizes_without_installing(self):
        expected = {"ready": True, "estimated_bytes": 10, "required_bytes": 20, "free_bytes": 30}
        with patch("app.api.config.preflight_qwen_tts_bundle", return_value=expected) as preflight:
            result = asyncio.run(preflight_qwen_tts(self.request(), self.payload()))
        self.assertEqual(result, expected)
        self.assertEqual(preflight.call_args.kwargs["model_path"].name, "Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf")

    def test_vulkan_install_uses_isolated_manifest_without_cuda_archive(self):
        captured = {}
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.api.config.settings.BASE_DIR", Path(directory) / "app"
        ), patch("app.api.config.hardware_service.amd_info", {"name": "Radeon"}), patch(
            "app.api.config.assemble_qwen_tts_bundle",
            side_effect=lambda **kwargs: captured.update(kwargs),
        ), patch(
            "app.api.config.inspect_qwen_tts_installation",
            return_value={"installed": True, "managed": True, "device": "vulkan"},
        ):
            result = asyncio.run(install_qwen_tts(self.request(), self.vulkan_payload()))
        self.assertEqual(result["device"], "vulkan")
        self.assertIsNone(captured["cuda_archive"])
        self.assertEqual(captured["device"], "vulkan")
        self.assertEqual(captured["manifest_path"].name, "runtime-manifest-vulkan.b10792.json")

    def test_vulkan_install_requires_detected_amd_hardware(self):
        with patch("app.api.config.hardware_service.amd_info", None):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(install_qwen_tts(self.request(), self.vulkan_payload()))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("AMD Vulkan", raised.exception.detail)

    def test_install_status_is_local_only_and_contains_no_paths(self):
        status = {"installed": True, "managed": True, "runtime_version": "test", "device": "cuda", "installed_bytes": 42}
        with patch("app.api.config.inspect_qwen_tts_installation", return_value=status):
            result = asyncio.run(get_qwen_tts_install_status(self.request()))
        self.assertEqual(result, status)
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(get_qwen_tts_install_status(self.request("192.0.2.10")))
        self.assertEqual(raised.exception.status_code, 403)

    def test_integrity_failure_is_a_client_error_without_path_disclosure(self):
        def reject(**_kwargs):
            raise QwenTTSInstallError("Model checksum mismatch")

        with patch("app.api.config.assemble_qwen_tts_bundle", reject):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(install_qwen_tts(self.request(), self.payload("D:/private/model")))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertNotIn("D:/private", raised.exception.detail)
        self.assertEqual(raised.exception.detail, "Model checksum mismatch")
        self.assertNotIn("Qwen3-TTS installation failed", raised.exception.detail)

    def test_openapi_exposes_local_install_contract(self):
        from app.main import app

        operation = app.openapi()["paths"]["/api/qwen-tts/install"]["post"]
        self.assertIn("requestBody", operation)
        self.assertIn("post", app.openapi()["paths"]["/api/qwen-tts/preflight"])
        self.assertIn("get", app.openapi()["paths"]["/api/qwen-tts/install-status"])
        schema = app.openapi()["components"]["schemas"]["QwenTTSInstallRequest"]
        self.assertEqual(set(schema["required"]), {"llama_archive", "model_directory"})
        self.assertIn("vulkan", schema["properties"]["device"]["pattern"])
