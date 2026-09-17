"""User-invoked upstream downloads with pinned hashes; no automatic startup download."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent


def download_ssl_context() -> ssl.SSLContext:
    # Keep system trust (including managed corporate roots), and supplement it
    # with the reviewed CA bundle already included in the portable runtime.
    import certifi
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(item: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = item['name']
    if Path(name).name != name or '\\' in name:
        raise ValueError('Invalid download filename')
    target = directory / name
    if target.exists():
        print(f'Checking existing file: {name}', flush=True)
        if sha256(target) == item['sha256']:
            print('SHA-256 matched; reusing the existing file.', flush=True)
            return target
        raise RuntimeError(f'Existing file has a different hash: {target}. Move it aside before retrying.')
    handle, temporary_name = tempfile.mkstemp(prefix='.' + name, suffix='.download', dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, 'wb') as output:
            request = urllib.request.Request(item['url'], headers={'User-Agent': 'VideoTranslator-user-setup'})
            with urllib.request.urlopen(request, timeout=60, context=download_ssl_context()) as source:
                if not source.geturl().startswith('https://'):
                    raise RuntimeError('Download redirected to an insecure URL')
                length = getattr(source, 'headers', {}).get('Content-Length')
                total = int(length) if length and str(length).isdigit() else None
                received = 0
                last_update = 0
                print(f'Downloading: {name}', flush=True)
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if now - last_update >= 1:
                        percent = f' / {total / 1024**2:.1f} MiB ({min(100, received * 100 / total):.0f}%)' if total else ''
                        print(f'\r{received / 1024**2:.1f} MiB{percent}', end='', flush=True)
                        last_update = now
                print(f'\rDownload complete: {received / 1024**2:.1f} MiB. Checking SHA-256...', flush=True)
        if sha256(temporary) != item['sha256']:
            raise RuntimeError(f'Download hash mismatch: {name}')
        os.replace(temporary, target)
        print('SHA-256 matched.', flush=True)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def install_ffmpeg(archive: Path, root: Path) -> None:
    destination = root / 'app' / 'ffmpeg'
    destination.mkdir(parents=True, exist_ok=True)
    # Copy only named binary/license members; never extract arbitrary ZIP paths.
    with zipfile.ZipFile(archive) as source:
        wanted = {'ffmpeg.exe': '/bin/ffmpeg.exe', 'ffprobe.exe': '/bin/ffprobe.exe',
                  'LICENSE': '/LICENSE', 'README.txt': '/README.txt'}
        members = {}
        for name, suffix in wanted.items():
            found = [p for p in source.namelist() if p.endswith(suffix)]
            if len(found) != 1:
                raise RuntimeError('Missing or ambiguous FFmpeg archive member: ' + name)
            members[name] = found[0]
        with tempfile.TemporaryDirectory(prefix='.ffmpeg-install-', dir=destination.parent) as temporary:
            staging = Path(temporary)
            for name, member in members.items():
                with source.open(member) as input_file, (staging / name).open('wb') as output:
                    shutil.copyfileobj(input_file, output)
            for name in members:
                existing = destination / name
                if existing.exists() and sha256(existing) != sha256(staging / name):
                    raise RuntimeError('Refusing to replace a different FFmpeg file: ' + str(existing))
            for name in members:
                os.replace(staging / name, destination / name)


def install_component(component: str, root: Path, manifest: dict) -> list[Path]:
    entry = manifest['components'][component]
    directory = root / 'upstream-downloads' / component
    if component == 'kokoro':
        directory = root / 'models' / 'kokoro'
    files = [download(item, directory) for item in entry['files']]
    if component == 'ffmpeg':
        install_ffmpeg(files[0], root)
    elif component == 'kokoro':
        wheel = next(path for path in files if path.suffix == '.whl')
        subprocess.run([str(root / 'runtime' / 'python.exe'), '-m', 'pip', 'install',
                        '--no-deps', '--no-index', str(wheel)], check=True)
    return files


def verify_installed(components: list[str]) -> int:
    report = ROOT.parent / 'upstream-install-report.json'
    with_args = [str(ROOT / 'runtime/python.exe'), '-B', str(ROOT / 'verify_upstream_install.py'),
                 '--components', *components, '--report', str(report)]
    result = subprocess.run(with_args)
    print(f'Installation report: {report}', flush=True)
    print('Installation checks passed.' if result.returncode == 0 else 'Installation checks failed. See the report; Qwen downloads still require installation in Settings.')
    return result.returncode


def interactive_menu(manifest: dict) -> int:
    actions = {'1': ('ffmpeg',), '2': ('kokoro',),
               '3': ('qwen-model', 'qwen-cuda'), '4': ('qwen-model', 'qwen-vulkan')}
    while True:
        print('\n1. Install and verify FFmpeg\n2. Install and verify Kokoro\n3. Download Qwen models and CUDA/CPU runtime\n4. Download Qwen models and Vulkan runtime\n5. Verify installed components\n0. Exit')
        choice = input('Select an option: ').strip()
        if choice == '0':
            return 0
        if choice == '5':
            selected = input('Components: ffmpeg kokoro qwen (Enter for all): ').split() or ['ffmpeg', 'kokoro', 'qwen']
            if any(value not in ('ffmpeg', 'kokoro', 'qwen') for value in selected):
                print('Invalid component name.')
                continue
            verify_installed(selected)
            continue
        if choice not in actions:
            print('Invalid option.')
            continue
        components = actions[choice]
        for component in components:
            print(json.dumps(manifest['components'][component], ensure_ascii=False, indent=2))
        if input('Review the upstream sources and terms above. Download/install? Type y to confirm: ').strip().lower() != 'y':
            print('Cancelled; nothing downloaded.')
            continue
        try:
            for component in components:
                install_component(component, ROOT, manifest)
            if choice in ('1', '2'):
                verify_installed(list(components))
            else:
                print('Downloads and SHA-256 checks complete. Open Settings: paths are filled automatically. Choose a device, preflight and install Qwen, then use menu 5 to verify the installation.')
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(f'Not completed: {error}. Resolve the issue and retry; matching cached files will be reused.')


def main() -> int:
    manifest = json.loads((ROOT / 'upstream-dependencies.json').read_text(encoding='utf-8'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', nargs='?', choices=sorted(manifest['components']))
    parser.add_argument('--accept-upstream-terms', action='store_true',
                        help='Confirm you reviewed the upstream license/source information below')
    args = parser.parse_args()
    if args.component is None:
        return interactive_menu(manifest)
    entry = manifest['components'][args.component]
    print(json.dumps(entry, ensure_ascii=False, indent=2), flush=True)
    if not args.accept_upstream_terms:
        print('Review the upstream terms and sources above, then rerun with --accept-upstream-terms. Nothing downloaded.')
        return 0
    files = install_component(args.component, ROOT, manifest)
    for path in files:
        print(path)
    if args.component.startswith('qwen-'):
        print('Open Settings: download paths are filled automatically. Choose a device, then preflight and install Qwen.')
    else:
        print('Installation complete. Please restart the application.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
