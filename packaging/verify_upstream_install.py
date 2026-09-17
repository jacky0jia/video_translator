"""Verify user-installed pinned dependencies without downloading or synthesizing."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import zipfile


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked(root: Path, relative: str, expected: str) -> None:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError('Installed file escapes application directory: ' + relative) from error
    if not path.is_file():
        raise RuntimeError('Missing installed file: ' + relative)
    if digest(path) != expected:
        raise RuntimeError('Installed file hash mismatch: ' + relative)


def verify_files(root: Path, selected: list[str]) -> dict:
    manifest = json.loads((root / 'upstream-dependencies.json').read_text(encoding='utf-8'))
    result = {}
    if 'ffmpeg' in selected:
        for item in manifest['components']['ffmpeg']['installed_files']:
            checked(root, item['path'], item['sha256'])
        result['ffmpeg'] = {'binary_hashes': True}
    if 'kokoro' in selected:
        items = manifest['components']['kokoro']['files']
        for item in items:
            checked(root, 'models/kokoro/' + item['name'], item['sha256'])
        wheel = root / 'models/kokoro' / next(item['name'] for item in items if item['name'].endswith('.whl'))
        count = 0
        with zipfile.ZipFile(wheel) as source:
            for name in source.namelist():
                if not name.startswith('espeakng_loader/') or name.endswith('/'):
                    continue
                with source.open(name) as stream:
                    expected = hashlib.file_digest(stream, 'sha256').hexdigest()
                checked(root, 'runtime/Lib/site-packages/' + name, expected)
                count += 1
        if not count:
            raise RuntimeError('Loader wheel contains no package files')
        result['kokoro'] = {'model_voice_hashes': True, 'loader_package_files': count, 'loader_hashes': True}
    if 'qwen' in selected:
        bundle_root = root / 'models/qwen3-tts'
        value = json.loads((bundle_root / 'bundle.json').read_text(encoding='utf-8'))
        for key, item in zip(('model_path', 'mmproj_path'), manifest['components']['qwen-model']['files'], strict=True):
            if value.get(key) != 'models/' + item['name']:
                raise RuntimeError('Unexpected installed Qwen model path: ' + key)
            checked(root, 'models/qwen3-tts/' + value[key], item['sha256'])
        if value.get('runtime_root') != 'runtime' or value.get('device') not in {'cpu', 'cuda', 'vulkan'}:
            raise RuntimeError('Unexpected Qwen runtime layout/device')
        filename = 'runtime-manifest-vulkan.b10792.json' if value['device'] == 'vulkan' else 'runtime-manifest.b10792.json'
        pinned = json.loads((root / 'packaging/llama-tts' / filename).read_text(encoding='utf-8'))
        for name, expected in pinned['files'].items():
            checked(root, 'models/qwen3-tts/runtime/' + name, expected)
        result['qwen'] = {'model_hashes': True, 'runtime_files': len(pinned['files']), 'runtime_hashes': True, 'device': value['device']}
    return result


def probe_application(root: Path) -> dict:
    environment = os.environ.copy()
    environment.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(root))
    environment['PATH'] = os.pathsep.join(str(root / p) for p in ('runtime', 'runtime/Scripts', 'runtime/Library/bin')) + os.pathsep + environment.get('PATH', '')
    code = 'import json; from app.services.upstream_dependencies import upstream_dependency_status; print(json.dumps(upstream_dependency_status()))'
    process = subprocess.run([str(root / 'runtime/python.exe'), '-B', '-c', code], cwd=root,
                             env=environment, capture_output=True, text=True, encoding='utf-8', timeout=60)
    if process.returncode:
        raise RuntimeError('Application dependency probe failed: ' + process.stderr[-1000:])
    return json.loads(process.stdout)


def write_report(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write('\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--components', nargs='+', choices=['ffmpeg', 'kokoro', 'qwen'], default=['ffmpeg', 'kokoro', 'qwen'])
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    report = args.report.resolve()
    if report.is_relative_to(root):
        parser.error('Store the report outside the application directory')
    result = {'platform': platform.platform(), 'root': str(root), 'selected': args.components,
              'scope': 'installed files and application recognition; no download, synthesis or clean-OS assertion'}
    try:
        result['files'] = verify_files(root, args.components)
        status = probe_application(root)
        result['application'] = status
        expected = {'ffmpeg': 'FFmpeg', 'kokoro': 'Kokoro', 'qwen': 'Qwen'}
        if not status.get('user_install') or any(expected[name] in status.get('missing', []) for name in args.components):
            raise RuntimeError('Application does not recognize all selected dependencies')
        result['passed'] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        result.update(passed=False, error=str(error))
    write_report(report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
