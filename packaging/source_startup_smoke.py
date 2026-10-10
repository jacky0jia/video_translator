"""Check source launchers in isolated environments without loading models."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request


def launcher_environment(python: Path, port: int) -> dict:
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIDEO_TRANSLATOR_SESSION_TOKEN"):
        environment.pop(name, None)
    environment.update(APP_PYTHON=str(python.resolve()), APP_PORT=str(port),
                       PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    return environment


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def check_response(path: str, body: bytes) -> None:
    if path == "/":
        if b"Video Translator" not in body:
            raise RuntimeError("Unexpected frontend response")
    elif path == "/api/tasks":
        if json.loads(body).get("tasks") != []:
            raise RuntimeError("Clean source snapshot contains unexpected tasks")
    elif path == "/api/config":
        if not isinstance(json.loads(body).get("config"), dict):
            raise RuntimeError("Configuration endpoint did not return settings")


def run_smoke(root: Path, python: Path, working_directory: Path, output: Path,
              timeout: float = 40, expect_missing: bool = False) -> dict:
    if os.name != "nt":
        raise RuntimeError("This smoke checks the Windows source launcher")
    root, python = root.resolve(strict=True), python.resolve(strict=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = launcher_environment(python, free_port())
    command = ["cmd.exe", "/d", "/c", "call", str(root / "start.bat")]
    if expect_missing:
        completed = subprocess.run(command, cwd=working_directory, env=environment,
                                   input="\n", capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
        if completed.returncode == 0 or "Missing Python packages:" not in completed.stdout:
            raise RuntimeError("Missing-dependency launch did not produce a useful failure: " + completed.stdout)
        report = {"missing_dependencies_detected": True, "exit_code": completed.returncode}
    else:
        log = output.with_suffix(".log")
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(command, cwd=working_directory, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                url = "http://127.0.0.1:" + environment["APP_PORT"]
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Source launcher exited early: " + log.read_text(encoding="utf-8", errors="replace"))
                    try:
                        with opener.open(url + "/", timeout=1) as response:
                            check_response("/", response.read())
                        break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Source launcher timed out: " + log.read_text(encoding="utf-8", errors="replace"))
                for path in ("/api/tasks", "/api/config"):
                    with opener.open(url + path, timeout=5) as response:
                        check_response(path, response.read())
                report = {"started": True, "frontend": True, "empty_history": True,
                          "configuration_api": True, "python": str(python),
                          "working_directory": str(working_directory.resolve()), "models_loaded": False}
            finally:
                if process.poll() is None:
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                    process.wait(timeout=10)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--working-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-missing", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_smoke(args.root, args.python, args.working_directory, args.output,
                               expect_missing=args.expect_missing), indent=2))
