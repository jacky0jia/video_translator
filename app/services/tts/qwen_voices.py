"""Read-only catalog for application-provided Qwen Base reference voices."""

from __future__ import annotations

import hashlib
import json
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.tts.base import TTSUnavailableError, TTSValidationError, VoiceInfo


_VOICE_ID = re.compile(r"^[a-z]{2}_[a-z]+_[1-9][0-9]*$")
_LANGUAGE_CODES = {
    "Chinese": "zh",
    "English": "en",
    "Japanese": "ja",
    "Korean": "ko",
}


@dataclass(frozen=True)
class QwenPresetVoice:
    id: str
    language: str
    presentation: str
    reference_path: Path
    sha256: str

    def public_info(self) -> VoiceInfo:
        return VoiceInfo(
            id=self.id,
            provider="qwen",
            language=self.language,
            gender=self.presentation,
            name=self.id,
            metadata={"source": "bundled_public_domain_reference"},
        )


class QwenVoiceCatalog:
    """Resolve neutral voice IDs to integrity-checked, catalog-owned WAV files."""

    def __init__(self, root: str | Path, *, verify_hashes: bool = True):
        self.root = Path(root).resolve(strict=True)
        self.verify_hashes = verify_hashes
        self._voices = self._load_manifest()

    def _load_manifest(self) -> dict[str, QwenPresetVoice]:
        manifest_path = self.root / "SOURCES.json"
        try:
            manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TTSUnavailableError("Qwen preset voice manifest is unavailable") from exc
        entries = manifest.get("voices") if isinstance(manifest, dict) else None
        if not isinstance(entries, list) or not entries:
            raise TTSUnavailableError("Qwen preset voice manifest contains no voices")

        voices: dict[str, QwenPresetVoice] = {}
        for entry in entries:
            voice = self._parse_entry(entry)
            if voice.id in voices:
                raise TTSUnavailableError(f"Duplicate Qwen preset voice id: {voice.id}")
            voices[voice.id] = voice
        return voices

    def _parse_entry(self, entry: Any) -> QwenPresetVoice:
        if not isinstance(entry, dict):
            raise TTSUnavailableError("Invalid Qwen preset voice entry")
        voice_id = entry.get("id")
        language_name = entry.get("language")
        presentation = entry.get("voice_presentation")
        filename = entry.get("file")
        expected_hash = entry.get("sha256")
        if not isinstance(voice_id, str) or not _VOICE_ID.fullmatch(voice_id):
            raise TTSUnavailableError("Invalid Qwen preset voice id")
        language = _LANGUAGE_CODES.get(language_name)
        if language is None or presentation not in {"female", "male"}:
            raise TTSUnavailableError(f"Invalid Qwen preset voice metadata: {voice_id}")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise TTSUnavailableError(f"Invalid Qwen preset voice filename: {voice_id}")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise TTSUnavailableError(f"Invalid Qwen preset voice hash: {voice_id}")

        reference_path = (self.root / filename).resolve(strict=True)
        try:
            reference_path.relative_to(self.root)
        except ValueError as exc:
            raise TTSUnavailableError(f"Qwen preset voice escapes catalog root: {voice_id}") from exc
        self._validate_wav(reference_path, voice_id)
        if self.verify_hashes and self._sha256(reference_path) != expected_hash:
            raise TTSUnavailableError(f"Qwen preset voice checksum mismatch: {voice_id}")
        return QwenPresetVoice(
            id=voice_id,
            language=language,
            presentation=presentation,
            reference_path=reference_path,
            sha256=expected_hash,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _validate_wav(path: Path, voice_id: str) -> None:
        try:
            with wave.open(str(path), "rb") as audio:
                valid = (
                    audio.getnchannels() == 1
                    and audio.getsampwidth() == 2
                    and audio.getframerate() == 24_000
                    and audio.getnframes() > 0
                )
        except (OSError, EOFError, wave.Error) as exc:
            raise TTSUnavailableError(f"Invalid Qwen preset voice WAV: {voice_id}") from exc
        if not valid:
            raise TTSUnavailableError(
                f"Qwen preset voice must be mono PCM-16 at 24000 Hz: {voice_id}"
            )

    def list(self, language: str | None = None) -> tuple[QwenPresetVoice, ...]:
        normalized = str(language or "").strip().lower()
        return tuple(
            voice
            for voice in self._voices.values()
            if not normalized or voice.language == normalized
        )

    def resolve(self, voice_id: str, language: str | None = None) -> QwenPresetVoice:
        try:
            voice = self._voices[voice_id]
        except KeyError as exc:
            raise TTSValidationError(f"Unknown Qwen preset voice: {voice_id}") from exc
        normalized = str(language or "").strip().lower()
        if normalized and voice.language != normalized:
            raise TTSValidationError(
                f"Qwen preset voice {voice_id} does not match language {normalized}"
            )
        return voice
