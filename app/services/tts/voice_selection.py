"""Resolve a requested voice against the active provider and language."""

from app.services.tts.registry import TTSRoute, tts_language_code


async def resolve_available_voice(registry, route: TTSRoute, requested: str, language: str) -> str:
    """Keep a valid voice or replace stale UI state with a compatible default."""
    language_code = tts_language_code(language)
    if route.provider_id == "edge":
        if str(requested or "").lower().startswith(f"{language_code}-"):
            return requested
        if route.default_voice:
            return route.default_voice

    voices = await registry.create(route.provider_id).list_voices()
    compatible = [
        voice for voice in voices
        if tts_language_code(getattr(voice, "language", "")) == language_code
    ]
    if any(getattr(voice, "id", "") == requested for voice in compatible):
        return requested
    if compatible:
        return compatible[0].id
    raise ValueError(
        f"No {route.provider_id} voice is available for language {language_code or language}"
    )
