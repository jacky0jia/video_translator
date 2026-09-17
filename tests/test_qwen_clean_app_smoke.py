import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "qwen_clean_app_smoke", ROOT / "packaging" / "qwen_clean_app_smoke.py"
)
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


def valid_values():
    preflight = {"ready": True}
    installed = {"installed": True, "managed": True}
    status = {"package_revision": 3, "device": "cuda"}
    voices = {
        "tts_mode": "qwen", "online_languages": [],
        "voices": [
            {"id": f"{language}_{gender}_1", "language": language}
            for language in ("zh", "en", "ja", "ko") for gender in ("female", "male")
        ],
    }
    return preflight, installed, status, voices, b"R" * 2048, "cuda"


def test_clean_app_result_requires_complete_local_catalog():
    values = valid_values()
    SMOKE.validate_result(*values)
    values[3]["voices"].pop()
    with pytest.raises(RuntimeError, match="catalog is incomplete"):
        SMOKE.validate_result(*values)


@pytest.mark.parametrize("index,update,match", [
    (0, {"ready": False}, "preflight"),
    (1, {"managed": False}, "managed installation"),
    (2, {"package_revision": 1}, "Unexpected"),
])
def test_clean_app_result_rejects_incomplete_install(index, update, match):
    values = list(valid_values())
    values[index].update(update)
    with pytest.raises(RuntimeError, match=match):
        SMOKE.validate_result(*values)
