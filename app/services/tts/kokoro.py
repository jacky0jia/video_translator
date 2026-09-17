"""In-process Kokoro ONNX provider adapter."""

import asyncio
import io
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Iterable, Mapping

from app.core.config import settings
from app.services.tts.audio import normalized_wav_result
from app.services.tts.base import ProviderHealth, TTSCancelled, TTSCapabilities, TTSSynthesisError, TTSSynthesisRequest, TTSSynthesisResult, TTSValidationError, VoiceInfo

logger = logging.getLogger(__name__)
VOICE_PREFIX_MAP = {
    "a": ("en-us", "en-us"), "b": ("en-gb", "en-gb"), "z": ("zh", "cmn"),
    "j": ("ja", "ja"), "e": ("es", "es"), "f": ("fr-fr", "fr-fr"),
    "h": ("hi", "hi"), "i": ("it", "it"), "p": ("pt-br", "pt-br"),
}
KOKORO_V1_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo", "ef_dora", "em_alex", "em_santa", "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola", "pf_dora", "pm_alex", "pm_santa",
)


def voice_metadata(voice_id: str) -> dict[str, str]:
    if not voice_id or len(voice_id) < 2:
        return {"id": voice_id, "language": "", "gender": ""}
    language, _ = VOICE_PREFIX_MAP.get(voice_id[0].lower(), ("", ""))
    code = voice_id[1].lower()
    gender = "female" if code == "f" else "male" if code == "m" else ""
    return {"id": voice_id, "language": language, "gender": gender}


def voice_language(voice_id: str) -> str:
    return VOICE_PREFIX_MAP.get((voice_id or "")[:1].lower(), ("", "en-us"))[1] or "en-us"


class KokoroTTSProvider:
    id = "kokoro"
    capabilities = TTSCapabilities(is_local=True, min_speed=0.5, max_speed=2.0)
    _MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
    _VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
    _kokoro_instance = None
    _kokoro_lock = threading.Lock()
    _voices_cache = None
    _zh_g2p = None
    _zh_g2p_lock = threading.Lock()
    _ja_g2p = None
    _ja_g2p_lock = threading.Lock()

    def __init__(self, synthesizer: Callable[[str, str, float], bytes] | None = None, voice_source: Callable[[], Iterable[Mapping[str, str]]] | None = None):
        self._synthesizer = synthesizer or self._synthesize_local
        self._voice_source = voice_source or self._local_voices

    def validate(self, request: TTSSynthesisRequest) -> None:
        if not request.text.strip():
            raise TTSValidationError("Kokoro text cannot be empty")
        if not request.voice:
            raise TTSValidationError("Kokoro voice is required")
        if not 0.5 <= request.speed <= 2.0:
            raise TTSValidationError("Kokoro speed must be in [0.5, 2.0]")
        if request.clone_sample_path:
            raise TTSValidationError("Kokoro does not support clone samples")

    async def synthesize(self, request: TTSSynthesisRequest, should_cancel=None) -> TTSSynthesisResult:
        self.validate(request)
        if should_cancel and should_cancel():
            raise TTSCancelled("Kokoro synthesis was cancelled")
        try:
            audio = await asyncio.to_thread(self._synthesizer, request.text, request.voice, request.speed)
        except TTSCancelled:
            raise
        except Exception as exc:
            raise TTSSynthesisError(f"Kokoro synthesis failed: {exc}") from exc
        if should_cancel and should_cancel():
            raise TTSCancelled("Kokoro synthesis was cancelled")
        return normalized_wav_result(audio, request.sample_rate)

    async def list_voices(self):
        return [VoiceInfo(id=v.get("id", ""), provider=self.id, name=v.get("name", ""), language=v.get("language", ""), gender=v.get("gender", ""), metadata={k: value for k, value in v.items() if k not in {"id", "name", "language", "gender"}}) for v in self._voice_source()]

    async def health(self):
        try:
            self._voice_source()
            return ProviderHealth(True, "Kokoro adapter is ready")
        except Exception as exc:
            return ProviderHealth(False, str(exc))

    @staticmethod
    def _default_model_dir() -> Path:
        return settings.BASE_DIR.parent / "models" / "kokoro"

    @staticmethod
    def _download_file(url: str, destination: str) -> None:
        import requests
        target = Path(destination)
        temporary = target.with_name(f".{target.name}.download")
        try:
            response = requests.get(url, stream=True, timeout=300)
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def _get_kokoro(cls):
        if cls._kokoro_instance is None:
            with cls._kokoro_lock:
                if cls._kokoro_instance is None:
                    if (settings.BASE_DIR.parent / 'upstream-dependencies.json').is_file():
                        import importlib.util
                        if importlib.util.find_spec('espeakng_loader') is None:
                            raise RuntimeError('Kokoro 外部依赖尚未安装，请按 README-PORTABLE.zh-CN.md 运行 install_upstream.py kokoro 后重新启动。')
                    try:
                        from kokoro_onnx import Kokoro
                    except ImportError as exc:
                        raise RuntimeError("本地 Kokoro 模式需要 kokoro-onnx 包。请运行: pip install kokoro-onnx soundfile") from exc
                    model_path = settings.KOKORO_MODEL_PATH or ""
                    voices_path = settings.KOKORO_VOICES_PATH or ""
                    if not model_path or not voices_path:
                        model_dir = cls._default_model_dir()
                        model_dir.mkdir(parents=True, exist_ok=True)
                        model_path = model_path or str(model_dir / "kokoro-v1.0.onnx")
                        voices_path = voices_path or str(model_dir / "voices-v1.0.bin")
                    if not Path(model_path).exists():
                        if (settings.BASE_DIR.parent / 'upstream-dependencies.json').is_file():
                            raise RuntimeError('Kokoro 模型尚未安装，请按 README-PORTABLE.zh-CN.md 从上游下载。')
                        logger.info("Downloading Kokoro model file...")
                        cls._download_file(cls._MODEL_URL, model_path)
                    if not Path(voices_path).exists():
                        if (settings.BASE_DIR.parent / 'upstream-dependencies.json').is_file():
                            raise RuntimeError('Kokoro 音色尚未安装，请按 README-PORTABLE.zh-CN.md 从上游下载。')
                        logger.info("Downloading Kokoro voice file...")
                        cls._download_file(cls._VOICES_URL, voices_path)
                    try:
                        cls._kokoro_instance = Kokoro(model_path, voices_path)
                    except Exception as exc:
                        uses_default_model = not settings.KOKORO_MODEL_PATH
                        if (settings.BASE_DIR.parent / 'upstream-dependencies.json').is_file() or not uses_default_model or "protobuf" not in str(exc).lower():
                            raise
                        logger.warning(
                            "Cached Kokoro model is invalid; downloading a fresh atomic replacement"
                        )
                        cls._download_file(cls._MODEL_URL, model_path)
                        cls._kokoro_instance = Kokoro(model_path, voices_path)
                    logger.info("Kokoro ONNX model loaded successfully.")
        return cls._kokoro_instance

    @classmethod
    def _get_zh_g2p(cls):
        if cls._zh_g2p is None:
            with cls._zh_g2p_lock:
                if cls._zh_g2p is None:
                    try:
                        from misaki.zh import ZHG2P
                    except ImportError as exc:
                        raise RuntimeError("中文音素化需要 misaki 包。请运行: pip install 'misaki[zh]'") from exc
                    cls._zh_g2p = ZHG2P()
        return cls._zh_g2p

    @classmethod
    def _get_ja_g2p(cls):
        if cls._ja_g2p is None:
            with cls._ja_g2p_lock:
                if cls._ja_g2p is None:
                    try:
                        from misaki.ja import JAG2P
                    except (ImportError, ModuleNotFoundError) as exc:
                        raise RuntimeError("日语 Kokoro 配音需要 Misaki 日语依赖。请运行: pip install 'misaki[ja]>=0.9.0'") from exc
                    cls._ja_g2p = JAG2P(version="pyopenjtalk")
        return cls._ja_g2p

    def _synthesize_local(self, text: str, voice: str, speed: float) -> bytes:
        kokoro = self._get_kokoro()
        try:
            import soundfile as sf
        except ImportError as exc:
            raise RuntimeError("本地 Kokoro 模式需要 soundfile 包。请运行: pip install soundfile") from exc

        def create_once(chunk: str):
            if voice[:1].lower() in {"z", "j"}:
                g2p = self._get_zh_g2p() if voice[:1].lower() == "z" else self._get_ja_g2p()
                converted = g2p(chunk)
                phonemes = converted[0] if isinstance(converted, tuple) else converted
                return kokoro.create(phonemes, voice=voice, speed=speed, is_phonemes=True)
            try:
                return kokoro.create(chunk, voice=voice, lang=voice_language(voice), speed=speed)
            except TypeError:
                return kokoro.create(chunk, voice=voice, lang=voice_language(voice))

        def create_samples(chunk: str):
            if len(chunk) > 180:
                left, right = self.split_text(chunk)
                if left and right:
                    return self._join_parts(create_samples(left), create_samples(right))
            try:
                return create_once(chunk)
            except IndexError as exc:
                if "out of bounds" not in str(exc) or len(chunk) < 2:
                    raise
                left, right = self.split_text(chunk)
                if not left or not right:
                    raise
                logger.warning("Kokoro input exceeded its phoneme limit; retrying as %s + %s characters", len(left), len(right))
                return self._join_parts(create_samples(left), create_samples(right))

        samples, sample_rate = create_samples(text)
        buffer = io.BytesIO()
        sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
        return buffer.getvalue()

    @staticmethod
    def _join_parts(left_result, right_result):
        left, left_rate = left_result
        right, right_rate = right_result
        if left_rate != right_rate:
            raise RuntimeError("Kokoro split synthesis returned inconsistent sample rates")
        import numpy as np
        pause = np.zeros(max(int(left_rate * 0.04), 1), dtype=left.dtype)
        return np.concatenate((left, pause, right)), left_rate

    @staticmethod
    def split_text(text: str) -> tuple[str, str]:
        midpoint = len(text) // 2
        candidates = [index + 1 for index, char in enumerate(text) if char in "。！？!?；;、，,：: " and 0 < index + 1 < len(text)]
        split_at = min(candidates, key=lambda value: abs(value - midpoint)) if candidates else midpoint
        return text[:split_at].strip(), text[split_at:].strip()

    def _local_voices(self):
        if self._voices_cache is None:
            voices = KOKORO_V1_VOICES
            if self._kokoro_instance is not None:
                try:
                    voices = sorted(self._kokoro_instance.get_voices()) or voices
                except Exception:
                    pass
            type(self)._voices_cache = [voice_metadata(voice) for voice in voices]
        return self._voices_cache
