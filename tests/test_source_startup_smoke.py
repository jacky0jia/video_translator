import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "source_startup_smoke", Path(__file__).resolve().parents[1] / "packaging" / "source_startup_smoke.py"
)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)
check_response = smoke.check_response
launcher_environment = smoke.launcher_environment


def test_launcher_resolves_its_directory_before_importing():
    launcher = (Path(__file__).resolve().parents[1] / "start.bat").read_text(encoding="utf-8")
    assert 'cd /d "%~dp0"\nif errorlevel 1 exit /b 1' in launcher
    assert launcher.index('cd /d "%~dp0"') < launcher.index('"%APP_PYTHON%" -c ')


def test_launcher_environment_isolates_python_and_session(monkeypatch, tmp_path):
    for name in ("PYTHONPATH", "PYTHONHOME", "VIDEO_TRANSLATOR_SESSION_TOKEN"):
        monkeypatch.setenv(name, "inherited")
    environment = launcher_environment(tmp_path / "python.exe", 12345)
    assert environment["APP_PYTHON"] == str((tmp_path / "python.exe").resolve())
    assert environment["APP_PORT"] == "12345"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert not {"PYTHONPATH", "PYTHONHOME", "VIDEO_TRANSLATOR_SESSION_TOKEN"} & environment.keys()


@pytest.mark.parametrize("path,body", [
    ("/", b"Video Translator"),
    ("/api/tasks", b'{"tasks": []}'),
    ("/api/config", b'{"config": {}}'),
])
def test_clean_startup_responses(path, body):
    check_response(path, body)


@pytest.mark.parametrize("path,body", [
    ("/", b"unrelated page"),
    ("/api/tasks", b'{"tasks": [1]}'),
    ("/api/config", b'{"config": null}'),
])
def test_unexpected_startup_responses_fail(path, body):
    with pytest.raises(RuntimeError):
        check_response(path, body)
