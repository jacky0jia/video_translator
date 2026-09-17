import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LlamaTTSPackagingTests(unittest.TestCase):
    def test_reviewed_manifest_is_minimal_and_fully_hashed(self):
        manifest = json.loads((
            ROOT / "packaging" / "llama-tts" / "runtime-manifest.b10792.json"
        ).read_text(encoding="utf-8"))
        files = manifest["files"]

        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["executable"], "llama-tts.exe")
        self.assertEqual(len(files), 25)
        for required in (
            "llama-tts.exe", "llama-common.dll", "llama.dll", "mtmd.dll",
            "ggml.dll", "ggml-base.dll", "ggml-cuda.dll", "cudart64_12.dll",
            "cublas64_12.dll", "cublasLt64_12.dll", "libomp.dll",
        ):
            self.assertIn(required, files)
        for filename, digest in files.items():
            self.assertEqual(Path(filename).name, filename)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertNotRegex(filename, r"server|rpc|bench|quantize|download")

    def test_notice_covers_all_redistributed_runtime_families(self):
        notice = (ROOT / "packaging" / "llama-tts" / "NOTICE.md").read_text(
            encoding="utf-8"
        )
        for phrase in ("MIT", "LLVM OpenMP", "NVIDIA CUDA Toolkit EULA", "Apache-2.0"):
            self.assertIn(phrase, notice)
        self.assertNotIn("TODO", notice)


if __name__ == "__main__":
    unittest.main()
