"""Task-owned raw speech cache; alignment and final tracks are always rebuilt."""

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import wave

from app.core.config import settings

logger = logging.getLogger(__name__)


def _file_identity(path: Path) -> dict:
    identity = {"path": str(path.resolve())}
    try:
        info = path.stat()
        identity.update(size=info.st_size, modified=info.st_mtime_ns)
        if path.suffix.lower() == ".json":
            identity["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        identity["missing"] = True
    return identity


def _tree_identity(root: Path) -> list:
    # File metadata avoids hashing multi-GB models on each dubbing run.
    return [_file_identity(path) for path in sorted(root.rglob("*")) if path.is_file()]


def synthesis_context(provider: str) -> dict:
    """Fingerprint synthesis inputs without persisting settings or credentials."""
    result = {"schema": 1, "provider": provider, "sample_rate": settings.DUB_SAMPLE_RATE}
    if provider == "kokoro":
        root = settings.BASE_DIR.parent / "models" / "kokoro"
        result["assets"] = [
            _file_identity(Path(settings.KOKORO_MODEL_PATH or root / "kokoro-v1.0.onnx")),
            _file_identity(Path(settings.KOKORO_VOICES_PATH or root / "voices-v1.0.bin")),
        ]
    elif provider in {"qwen", "cosyvoice"}:
        root = settings.BASE_DIR.parent / "models" / ("qwen3-tts" if provider == "qwen" else "cosyvoice")
        manifest = root / "bundle.json"
        assets = [_file_identity(manifest)]
        try:
            bundle = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(bundle, dict):
                for name in ("model_path", "mmproj_path", "runtime_manifest", "artifact_manifest", "runtime_script"):
                    if isinstance(bundle.get(name), str) and bundle[name]:
                        path = Path(bundle[name])
                        assets.append(_file_identity(path if path.is_absolute() else root / path))
                for name in ("model_dir", "reference_root"):
                    if isinstance(bundle.get(name), str) and bundle[name]:
                        path = Path(bundle[name])
                        assets.extend(_tree_identity(path if path.is_absolute() else root / path))
        except (OSError, ValueError):
            pass
        if provider == "qwen":
            assets.extend(_tree_identity(settings.BASE_DIR.parent / "models" / "voices" / "librivox_public_domain"))
            result["cpu_fallback"] = settings.QWEN_AUTO_CPU_FALLBACK
        result["assets"] = assets
    elif provider == "speaches":
        result.update(endpoint=settings.TTS_API_URL, model=settings.TTS_MODEL,
                      credential=settings.TTS_API_KEY)
        if settings.TTS_VOICES_DIR:
            result["voices"] = _tree_identity(Path(settings.TTS_VOICES_DIR))
    return result


def synthesis_signature(provider: str) -> str:
    payload = json.dumps(synthesis_context(provider), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SpeechCache:
    def __init__(self, root: Path, context: dict, *, speed: float, language: str):
        self.root = root
        self.context = dict(context, speed=speed, language=language)
        self.sample_rate = context["sample_rate"]

    def _key(self, text: str, voice: str) -> str:
        payload = json.dumps(dict(self.context, text=text, voice=voice), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _valid_audio(self, audio: bytes) -> bool:
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                frames = wav.getnframes()
                return (wav.getnchannels() == 1 and wav.getsampwidth() == 2
                        and wav.getframerate() == self.sample_rate and frames > 0
                        and len(wav.readframes(frames)) == frames * 2)
        except (OSError, EOFError, wave.Error):
            return False

    def get(self, text: str, voice: str) -> bytes | None:
        key = self._key(text, voice)
        try:
            metadata = json.loads((self.root / f"{key}.json").read_text(encoding="utf-8"))
            audio = (self.root / f"{key}.wav").read_bytes()
            if (isinstance(metadata, dict) and metadata.get("key") == key
                    and metadata.get("sha256") == hashlib.sha256(audio).hexdigest()
                    and self._valid_audio(audio)):
                return audio
        except (OSError, ValueError):
            pass
        return None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def put(self, text: str, voice: str, audio: bytes) -> None:
        if not self._valid_audio(audio):
            return
        key = self._key(text, voice)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._atomic_write(self.root / f"{key}.wav", audio)
            metadata = {"key": key, "sha256": hashlib.sha256(audio).hexdigest()}
            self._atomic_write(self.root / f"{key}.json", json.dumps(metadata).encode("utf-8"))
        except OSError as exc:
            # Caching is an optimization; newly synthesized speech remains usable.
            logger.warning("Could not cache raw speech: %s", exc)
