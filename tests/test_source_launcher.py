from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_source_launcher_changes_to_its_own_directory_before_importing():
    launcher = (ROOT / "start.bat").read_text(encoding="utf-8")
    assert 'cd /d "%~dp0"' in launcher
    assert launcher.index('cd /d "%~dp0"') < launcher.index('"%APP_PYTHON%" -c ')
    assert 'cd /d "%~dp0"\nif errorlevel 1 exit /b 1' in launcher


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
