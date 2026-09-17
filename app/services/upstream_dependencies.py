"""Report user-installed optional dependencies without downloading or loading models."""
import importlib.util
import json
from pathlib import Path

from app.core.config import settings


def qwen_upstream_sources() -> dict:
    root = settings.BASE_DIR.parent
    manifest = root / 'upstream-dependencies.json'
    if not manifest.is_file():
        return {}
    components = json.loads(manifest.read_text(encoding='utf-8'))['components']

    def path(component, index):
        name = components[component]['files'][index]['name']
        if Path(name).name != name or '\\' in name:
            raise ValueError('Invalid upstream filename')
        return str(root / 'upstream-downloads' / component / name)

    return {
        'cuda': {'llama_archive': path('qwen-cuda', 0), 'cuda_archive': path('qwen-cuda', 1)},
        'vulkan': {'llama_archive': path('qwen-vulkan', 0), 'cuda_archive': ''},
        'model_directory': str(root / 'upstream-downloads/qwen-model'),
    }


def upstream_dependency_status() -> dict:
    root = settings.BASE_DIR.parent
    if not (root / 'upstream-dependencies.json').is_file():
        return {'user_install': False, 'missing': []}
    from app.services.tts.qwen_installer import inspect_qwen_tts_installation

    missing = []
    if not settings.ffmpeg_path:
        missing.append('FFmpeg')
    model = Path(settings.KOKORO_MODEL_PATH) if settings.KOKORO_MODEL_PATH else root / 'models/kokoro/kokoro-v1.0.onnx'
    voices = Path(settings.KOKORO_VOICES_PATH) if settings.KOKORO_VOICES_PATH else root / 'models/kokoro/voices-v1.0.bin'
    if not model.is_file() or not voices.is_file() or importlib.util.find_spec('espeakng_loader') is None:
        missing.append('Kokoro')
    if not inspect_qwen_tts_installation(root / 'models/qwen3-tts').get('installed'):
        missing.append('Qwen')
    return {'user_install': True, 'missing': missing}
