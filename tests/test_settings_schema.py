import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from app.api.capabilities import get_capabilities
from app.api.config import get_config, get_config_schema, update_config
from app.core.capabilities import STANDARD_CAPABILITIES
from app.core.config import Settings
from app.core.config_migrations import migrate_config
from app.core.settings_schema import SETTING_DEFINITIONS, get_settings_schema
from fastapi import HTTPException


class SettingsSchemaTests(unittest.TestCase):
    def test_language_save_survives_a_new_settings_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Settings, "CONFIG_PATH", Path(directory) / "config.yaml"):
                config = Settings()
                self.assertEqual(config.UI_LANGUAGE, "")
                for language in ("zh", "en"):
                    config.update({"UI_LANGUAGE": language})
                    restarted = Settings()
                    self.assertEqual(restarted.to_public_dict()["UI_LANGUAGE"], language)
                with self.assertRaises(ValueError):
                    config.update({"UI_LANGUAGE": "invalid"})
                self.assertEqual(Settings().UI_LANGUAGE, "en")

    def test_registry_keys_are_unique_and_defaults_validate(self):
        keys = [item.key for item in SETTING_DEFINITIONS]
        self.assertEqual(len(keys), len(set(keys)))
        schema = get_settings_schema(is_local=True)
        self.assertEqual(schema["schema_version"], 1)
        self.assertEqual({field["key"] for field in schema["fields"]}, set(keys))
        tts_mode = next(field for field in schema["fields"] if field["key"] == "TTS_MODE")
        self.assertEqual(tts_mode["choices"], ["kokoro", "edge", "qwen"])

    def test_remote_schema_omits_local_only_fields(self):
        schema = get_settings_schema(is_local=False)
        keys = {field["key"] for field in schema["fields"]}
        self.assertNotIn("ASR_MODEL_PATH", keys)
        self.assertNotIn("FFMPEG_PATH", keys)
        self.assertNotIn("LM_STUDIO_CLI_PATH", keys)
        self.assertNotIn("KOKORO_MODEL_PATH", keys)
        self.assertNotIn("UI_LANGUAGE", keys)

    def test_schema_is_stable_and_secret_defaults_are_never_exposed(self):
        first = get_settings_schema(is_local=True)
        second = get_settings_schema(is_local=True)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, separators=(",", ":")),
            json.dumps(second, ensure_ascii=False, separators=(",", ":")),
        )
        section_order = {section["id"]: section["order"] for section in first["sections"]}
        positions = [(section_order[field["section"]], field["order"]) for field in first["fields"]]
        self.assertEqual(positions, sorted(positions))
        for field in first["fields"]:
            if field["secret"]:
                self.assertEqual(field["default"], "")

    def test_schema_api_preserves_request_locality(self):
        local = asyncio.run(get_config_schema(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))))
        remote = asyncio.run(get_config_schema(SimpleNamespace(client=SimpleNamespace(host="192.0.2.10"))))
        self.assertGreater(len(local["fields"]), len(remote["fields"]))

    def test_standard_capabilities_are_read_only_defaults(self):
        result = asyncio.run(get_capabilities())
        self.assertEqual(result["edition"], "standard")
        self.assertEqual(result, STANDARD_CAPABILITIES.to_public_dict())
        self.assertFalse(result["capabilities"]["voice_clone_managed"])
        self.assertFalse(result["capabilities"]["voice_design"])
        self.assertFalse(result["capabilities"]["theme_packages"])

    def test_legacy_config_api_shapes_are_preserved(self):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        public = {"LLM_API_KEY": "********", "TTS_MODE": "kokoro"}
        fake_settings = SimpleNamespace(
            to_public_dict=lambda: public,
            update=lambda updates: None,
        )
        with patch("app.api.config.settings", fake_settings):
            fetched = asyncio.run(get_config(request))
            updated = asyncio.run(update_config(request, {"TTS_MODE": "kokoro"}))
        self.assertEqual(fetched, {"config": public, "is_local": True})
        self.assertEqual(updated, {"status": "success", "config": public})

    def test_remote_config_update_filters_all_local_only_fields(self):
        request = SimpleNamespace(client=SimpleNamespace(host="192.0.2.10"))
        captured = {}
        fake_settings = SimpleNamespace(
            to_public_dict=lambda: {},
            update=lambda updates: captured.update(updates),
        )
        submitted = {
            "LLM_PROVIDER": "ollama",
            "ASR_MODEL_PATH": "private/asr",
            "LM_STUDIO_CLI_PATH": "private/lms.exe",
            "KOKORO_MODEL_PATH": "private/kokoro.onnx",
            "FFMPEG_PATH": "private/ffmpeg.exe",
            "UI_LANGUAGE": "zh",
        }
        with patch("app.api.config.settings", fake_settings):
            asyncio.run(update_config(request, submitted))
        self.assertEqual(captured, {"LLM_PROVIDER": "ollama"})

    def test_config_api_validation_error_has_field_without_secret_value(self):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        submitted_secret = "do-not-echo-this-secret"

        def reject(_updates):
            raise ValueError("Field 'LLM_API_BASE_URL' must use http or https scheme.")

        fake_settings = SimpleNamespace(update=reject, to_public_dict=lambda: {})
        with patch("app.api.config.settings", fake_settings):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(update_config(request, {
                    "LLM_API_BASE_URL": "not-a-url",
                    "LLM_API_KEY": submitted_secret,
                }))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("LLM_API_BASE_URL", raised.exception.detail)
        self.assertNotIn(submitted_secret, raised.exception.detail)


class ConfigMigrationTests(unittest.TestCase):
    def test_legacy_local_tts_is_migrated_idempotently(self):
        migrated = migrate_config({"TTS_MODE": "local", "FUTURE_EXTENSION": "keep"})
        self.assertEqual(migrated["CONFIG_VERSION"], 1)
        self.assertEqual(migrated["TTS_MODE"], "kokoro")
        self.assertEqual(migrated["FUTURE_EXTENSION"], "keep")
        self.assertEqual(migrate_config(migrated), migrated)

    def test_masked_secret_is_preserved_and_empty_secret_clears(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            with patch.object(Settings, "CONFIG_PATH", path):
                config = Settings()
                config.LLM_API_KEY = "secret"
                config.update({"LLM_API_KEY": "********", "LLM_TEMPERATURE": "0.7"})
                self.assertEqual(config.LLM_API_KEY, "secret")
                self.assertEqual(config.LLM_TEMPERATURE, 0.7)
                config.update({"LLM_API_KEY": ""})
                self.assertEqual(config.LLM_API_KEY, "")

    def test_persisted_mask_placeholder_is_not_loaded_as_a_real_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("LLM_API_KEY: '********'\n", encoding="utf-8")
            with patch.object(Settings, "CONFIG_PATH", path):
                config = Settings()
                self.assertEqual(config.LLM_API_KEY, "")
                config.update({"TTS_SPEED": 1.0})
            saved = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["LLM_API_KEY"], "")

    def test_invalid_multi_field_update_is_transactional(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            with patch.object(Settings, "CONFIG_PATH", path):
                config = Settings()
                old_provider = config.LLM_PROVIDER
                old_temperature = config.LLM_TEMPERATURE
                with self.assertRaises(ValueError):
                    config.update({"LLM_PROVIDER": "ollama", "LLM_TEMPERATURE": 9})
                self.assertEqual(config.LLM_PROVIDER, old_provider)
                self.assertEqual(config.LLM_TEMPERATURE, old_temperature)
                self.assertFalse(path.exists())

    def test_save_preserves_unknown_fields_and_writes_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("TTS_MODE: local\nFUTURE_EXTENSION: keep\n", encoding="utf-8")
            with patch.object(Settings, "CONFIG_PATH", path):
                config = Settings()
                self.assertEqual(config.TTS_MODE, "kokoro")
                config.update({"TTS_SPEED": 1.2})
            saved = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["CONFIG_VERSION"], 1)
            self.assertEqual(saved["FUTURE_EXTENSION"], "keep")
            self.assertEqual(saved["TTS_MODE"], "kokoro")

    def test_qwen_cpu_fallback_loads_publishes_and_persists_as_known_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("CONFIG_VERSION: 1\nQWEN_AUTO_CPU_FALLBACK: false\n", encoding="utf-8")
            with patch.object(Settings, "CONFIG_PATH", path):
                config = Settings()
                self.assertFalse(config.QWEN_AUTO_CPU_FALLBACK)
                self.assertNotIn("QWEN_AUTO_CPU_FALLBACK", config._unknown_config)
                self.assertFalse(config.to_public_dict()["QWEN_AUTO_CPU_FALLBACK"])
                config.update({"QWEN_AUTO_CPU_FALLBACK": True})
            saved = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertTrue(saved["QWEN_AUTO_CPU_FALLBACK"])

    def test_atomic_replace_failure_rolls_back_memory_and_preserves_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            original = "CONFIG_VERSION: 1\nTTS_SPEED: 1.0\n"
            path.write_text(original, encoding="utf-8")
            with patch.object(Settings, "CONFIG_PATH", path):
                config = Settings()
                with patch("app.core.config.os.replace", side_effect=OSError("disk unavailable")):
                    with self.assertRaisesRegex(OSError, "disk unavailable"):
                        config.update({"TTS_SPEED": 1.25})
                self.assertEqual(config.TTS_SPEED, 1.0)
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_damaged_yaml_uses_defaults_without_overwriting_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            damaged = "TTS_SPEED: [unterminated\n"
            path.write_text(damaged, encoding="utf-8")
            with patch.object(Settings, "CONFIG_PATH", path), self.assertLogs(
                "app.core.config", level="ERROR"
            ) as captured:
                config = Settings()
            self.assertEqual(config.TTS_SPEED, Settings.TTS_SPEED)
            self.assertEqual(path.read_text(encoding="utf-8"), damaged)
            self.assertTrue(any("Failed to load config file" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
