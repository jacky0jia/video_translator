from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_source_launcher_uses_one_configurable_python_environment():
    launcher = (ROOT / "start.bat").read_text(encoding="utf-8")

    assert 'if not defined APP_PYTHON set "APP_PYTHON=python"' in launcher
    assert '"%APP_PYTHON%" -c ' in launcher
    assert "('uvicorn','faster_whisper')" in launcher
    assert '"%APP_PYTHON%" -m uvicorn app.main:app' in launcher
    assert "\nuvicorn app.main:app" not in launcher
    assert "exit /b %APP_EXIT_CODE%" in launcher


def test_source_setup_docs_explain_python_override():
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    for document in (english, chinese):
        assert "APP_PYTHON" in document
        assert "requirements.txt" in document
