import unittest

from app.services.tts.base import TTSCapabilities
from app.services.tts.qwen_bundle import qwen_worker_config_for_host
from app.services.tts.registry import (
    EDGE_DEFAULT_VOICES,
    KOREAN_DEFAULT_VOICE,
    TTSProviderDescriptor,
    TTSProviderRegistry,
    normalize_tts_mode,
    resolve_tts_route,
)


class TTSProviderRegistryTests(unittest.TestCase):
    def test_qwen_selects_device_for_current_host_instead_of_builder(self):
        cuda = {"device": "cuda", "runtime_root": "fixture"}
        self.assertEqual(
            qwen_worker_config_for_host(
                cuda, nvidia_available=True, amd_available=False, auto_cpu_fallback=True
            )["device"],
            "cuda",
        )
        cpu_host = qwen_worker_config_for_host(
            cuda, nvidia_available=False, amd_available=True, auto_cpu_fallback=True
        )
        self.assertEqual(cpu_host["device"], "cpu")
        self.assertTrue(cpu_host["auto_cpu_fallback"])
        self.assertEqual(
            qwen_worker_config_for_host(
                {"device": "cpu"}, nvidia_available=True, amd_available=False,
                auto_cpu_fallback=False
            ),
            {"device": "cpu", "auto_cpu_fallback": False},
        )
        vulkan = {"device": "vulkan", "runtime_root": "fixture"}
        self.assertEqual(qwen_worker_config_for_host(
            vulkan, nvidia_available=False, amd_available=True, auto_cpu_fallback=True
        )["device"], "vulkan")
        self.assertEqual(qwen_worker_config_for_host(
            vulkan, nvidia_available=True, amd_available=False, auto_cpu_fallback=True
        )["device"], "cpu")

    def test_legacy_local_mode_normalizes_to_kokoro(self):
        self.assertEqual(normalize_tts_mode("local"), "kokoro")
        self.assertEqual(resolve_tts_route("local", "Chinese").provider_id, "kokoro")

    def test_edge_routes_all_supported_languages_with_compatible_defaults(self):
        languages = {"Chinese": "zh", "English": "en", "Japanese": "ja", "Korean": "ko"}
        for language, code in languages.items():
            with self.subTest(language=language):
                route = resolve_tts_route("edge", language)
                self.assertEqual(route.provider_id, "edge")
                self.assertEqual(route.default_voice, EDGE_DEFAULT_VOICES[code])
        self.assertEqual(EDGE_DEFAULT_VOICES["ko"], KOREAN_DEFAULT_VOICE)

    def test_kokoro_korean_is_not_silently_routed_online(self):
        route = resolve_tts_route("kokoro", "Korean")
        self.assertEqual(route.provider_id, "kokoro")
        self.assertIsNone(route.default_voice)

    def test_qwen_keeps_korean_offline(self):
        route = resolve_tts_route("qwen", "Korean")
        self.assertEqual(route.provider_id, "qwen")
        self.assertIsNone(route.default_voice)

    def test_configured_non_korean_provider_is_preserved(self):
        route = resolve_tts_route("speaches", "Japanese")
        self.assertEqual(route.provider_id, "speaches")
        self.assertIsNone(route.default_voice)

    def test_unknown_and_duplicate_providers_are_rejected(self):
        descriptor = TTSProviderDescriptor(
            id="example", display_name="Example", capabilities=TTSCapabilities()
        )
        registry = TTSProviderRegistry((descriptor,))
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(descriptor)
        with self.assertRaisesRegex(ValueError, "Unknown TTS provider"):
            registry.resolve("missing", "English")


if __name__ == "__main__":
    unittest.main()
