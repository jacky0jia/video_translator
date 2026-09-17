"""File-level public core policy, shared by build and independent verification."""
from pathlib import Path


def assert_upstream_assets_absent(bundle: Path) -> None:
    """The public core must not contain dependencies delegated to the user."""
    forbidden = []
    for path in bundle.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(bundle)
        parts = tuple(part.lower() for part in relative.parts)
        name = path.name.lower()
        if (name in {'ffmpeg.exe', 'ffprobe.exe', 'libespeak-ng.dll', 'espeak-ng.dll'}
                or path.suffix.lower() == '.gguf'
                or name.startswith('espeakng_loader-') and path.suffix.lower() == '.whl'
                or name.startswith('kokoro') and path.suffix.lower() == '.onnx'
                or name == 'voices-v1.0.bin'
                or 'espeakng_loader' in parts
                or 'espeak-ng-data' in parts
                or any(p.startswith('espeakng_loader-') and p.endswith('.dist-info') for p in parts)
                or parts[:2] == ('models', 'qwen3-tts')):
            forbidden.append(relative.as_posix())
    if forbidden:
        raise RuntimeError('Public core contains upstream-only assets: ' + ', '.join(forbidden))
