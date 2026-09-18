import unittest
from pathlib import Path


class PublicQwenDocumentationTests(unittest.TestCase):
    def test_readmes_cover_current_release_and_settings_routes(self):
        from app.core.settings_schema import SETTINGS_BY_KEY

        root = Path(__file__).resolve().parents[1]
        for filename in ("README.md", "README.zh-CN.md"):
            document = (root / filename).read_text(encoding="utf-8")
            self.assertIn("releases/tag/v0.1.0-alpha.1", document)
            self.assertIn("start-portable.bat", document)
            self.assertIn("install-upstream.bat", document)
            mode_line = next(line for line in document.splitlines() if "`TTS_MODE`" in line)
            for mode in SETTINGS_BY_KEY["TTS_MODE"].choices:
                self.assertIn(f"`{mode}`", mode_line)
            self.assertIn("RX 5700 XT", document)
            self.assertIn("Vulkan", document)
            self.assertIn("UI_LANGUAGE", document)
            self.assertIn("MIT", document)
            self.assertIn("API", document)
            self.assertIn("ASR small", document)
            self.assertNotIn("v0.1.0-alpha.0", document)
        english = (root / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("no Windows installer or portable release yet", english)
        self.assertIn("without per-minute cloud API fees", english)
        self.assertIn("does not automatically detect idle hardware", english)

    def test_readmes_describe_the_same_private_worker_boundary(self):
        root = Path(__file__).resolve().parents[1]
        english = (root / "README.md").read_text(encoding="utf-8")
        chinese = (root / "README.zh-CN.md").read_text(encoding="utf-8")

        for document in (english, chinese):
            self.assertIn("Qwen3-TTS-12Hz-1.7B-Base-GGUF", document)
            self.assertIn("LibriVox", document)
            self.assertIn("`qwen-tts`", document)
            self.assertIn("`llama-tts`", document)

        self.assertIn("private `llama-tts` worker", english)
        self.assertIn("私有 `llama-tts` worker", chinese)
        self.assertNotIn("until LM Studio exposes", english)

    def test_notices_record_model_and_voice_asset_redistribution_metadata(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.zh-CN.md"):
            notice = (root / filename).read_text(encoding="utf-8")
            self.assertIn("Qwen3-TTS-12Hz-1.7B-Base-GGUF", notice)
            self.assertIn("LibriVox", notice)
            self.assertIn("SOURCES.json", notice)


if __name__ == "__main__":
    unittest.main()
