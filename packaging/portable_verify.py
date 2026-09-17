"""Verify and smoke-test a built Video Translator portable directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
from upstream_policy import assert_upstream_assets_absent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(bundle: Path) -> dict:
    bundle = bundle.resolve()
    manifest = json.loads((bundle / "portable-manifest.json").read_text(encoding="utf-8"))
    scope = manifest.get("distribution_scope")
    if scope not in {"local_acceptance_only", "public_release"}:
        raise RuntimeError("Unexpected portable distribution scope")
    if scope == "public_release" and (
        not manifest.get("public_distribution_ready")
        or manifest.get("public_distribution_blockers")
    ):
        raise RuntimeError("Public portable bundle has unresolved distribution blockers")
    if scope == "public_release":
        if manifest.get('dependency_delivery') == 'user_upstream_install':
            assert_upstream_assets_absent(bundle)
            if not (bundle / 'upstream-dependencies.json').is_file():
                raise RuntimeError('Public core has no upstream installation manifest')
        licenses_path = bundle / "licenses" / "THIRD-PARTY-MANIFEST.json"
        if not licenses_path.is_file():
            raise RuntimeError("Public portable bundle has no third-party license manifest")
        licenses = json.loads(licenses_path.read_text(encoding="utf-8"))
        if licenses.get("public_distribution_blockers"):
            raise RuntimeError("Third-party license manifest has unresolved distribution blockers")
    listed = {entry["path"]: entry for entry in manifest.get("files", [])}
    actual = {
        path.relative_to(bundle).as_posix(): path
        for path in bundle.rglob("*") if path.is_file() and path.name != "portable-manifest.json"
    }
    if set(listed) != set(actual):
        missing = sorted(set(listed) - set(actual))
        extra = sorted(set(actual) - set(listed))
        raise RuntimeError(f"Portable file inventory mismatch; missing={missing}, extra={extra}")
    for name, path in actual.items():
        entry = listed[name]
        if entry.get("bytes") != path.stat().st_size or entry.get("sha256") != sha256(path):
            raise RuntimeError(f"Portable file checksum mismatch: {name}")
    return manifest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _read_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def smoke_server(bundle: Path, timeout: float = 60.0) -> dict:
    bundle = bundle.resolve()
    history = bundle / "app" / "history.json"
    history_existed = history.exists()
    port = _free_port()
    environment = os.environ.copy()
    environment["APP_PORT"] = str(port)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        [str(bundle / "runtime/python.exe"), str(bundle / "portable_launcher.py"), "--no-browser", "--port", str(port)],
        cwd=bundle, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise RuntimeError(f"Portable server exited early ({process.returncode}): {output[-2000:]}")
            try:
                with urllib.request.urlopen(url + "/", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.25)
        else:
            raise RuntimeError("Portable server did not become ready")
        install = _read_json(url + "/api/qwen-tts/install-status")
        voices = _read_json(url + "/api/dubbing/voices")
        core = (bundle / 'upstream-dependencies.json').is_file()
        if core and install.get('installed'):
            raise RuntimeError('Public core unexpectedly has a Qwen installation')
        if not core and (not install.get("installed") or not install.get("managed")):
            raise RuntimeError("Bundled Qwen installation is unavailable")
        ids = {voice["id"] for voice in voices.get("voices", [])}
        if core:
            if ids:
                raise RuntimeError('Public core unexpectedly exposes installed Qwen voices')
            config = _read_json(url + '/api/config')
            dependencies = config.get('upstream_dependencies', {})
            if not dependencies.get('user_install') or 'Qwen' not in dependencies.get('missing', []):
                raise RuntimeError('Public core does not report missing upstream dependencies')
            with urllib.request.urlopen(url + '/api/dependencies/install-guide', timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError('Public core installation guide is unavailable')
        elif len(ids) != 8 or "ja_male_1" not in ids:
            raise RuntimeError(f"Unexpected portable voice catalog: {sorted(ids)}")
        return {"http_status": 200, "qwen": install, "voice_ids": sorted(ids), "port": port}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(10)
        if not history_existed:
            history.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--skip-hashes", action="store_true")
    args = parser.parse_args()
    manifest = None if args.skip_hashes else verify_manifest(args.bundle)
    result = smoke_server(args.bundle)
    if not args.skip_hashes:
        verify_manifest(args.bundle)
    manifest_summary = None if manifest is None else {
        "source_commit": manifest["source_commit"],
        "file_count": len(manifest["files"]),
        "payload_bytes": sum(entry["bytes"] for entry in manifest["files"]),
        "distribution_scope": manifest["distribution_scope"],
    }
    print(json.dumps({"manifest": manifest_summary, "smoke": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
