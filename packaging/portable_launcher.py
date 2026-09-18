"""Launcher copied into the root of a Video Translator portable bundle."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import threading
import time
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parent


def validate_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"Port {port} is already in use. Close the other application or set APP_PORT."
            ) from exc
    return port


def validate_layout(root: Path = ROOT) -> None:
    required = [
        "app/main.py",
        "app/frontend/dist/index.html",
        "models/faster-whisper-small/model.bin",
        "models/voices/librivox_public_domain/SOURCES.json",
        "runtime/python.exe",
    ]
    if not (root / 'upstream-dependencies.json').is_file():
        required.extend(['app/ffmpeg/ffmpeg.exe', 'app/ffmpeg/ffprobe.exe'])
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise RuntimeError("Portable bundle is incomplete: " + ", ".join(missing))


def open_when_ready(url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.25)


def prepare_environment(root: Path = ROOT) -> None:
    os.chdir(root)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONPATH"] = str(root)
    runtime = root / "runtime"
    additions = (runtime, runtime / "Scripts", runtime / "Library" / "bin")
    os.environ["PATH"] = os.pathsep.join(map(str, additions)) + os.pathsep + os.environ.get("PATH", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Video Translator from its portable directory")
    parser.add_argument("--port", type=int, default=int(os.environ.get("APP_PORT", "8769")))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    validate_layout()
    port = validate_port(args.port)
    prepare_environment()
    url = f"http://127.0.0.1:{port}/"
    print(f"Video Translator portable: {url}", flush=True)
    print("Press Ctrl+C to stop the application.", flush=True)
    if (ROOT / 'upstream-dependencies.json').is_file():
        print('Install external dependencies separately. Read README-PORTABLE.md before first use.', flush=True)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    import uvicorn

    uvicorn.run("app.main:app", app_dir=str(ROOT), host="127.0.0.1", port=port, proxy_headers=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
