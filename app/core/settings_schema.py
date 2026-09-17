"""Typed metadata and value normalization for user-editable settings."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = 1
MASKED_SECRET = "********"


class SettingSection(str, Enum):
    TRANSCRIPTION = "transcription"
    TRANSLATION = "translation"
    DUBBING = "dubbing"
    VIDEO = "video"
    SYSTEM = "system"


class SettingLevel(str, Enum):
    BASIC = "basic"
    ADVANCED = "advanced"


class ApplyPolicy(str, Enum):
    IMMEDIATE = "immediate"
    NEXT_TASK = "next_task"
    RESTART = "restart"


class VisibilityRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    operator: Literal["eq", "in", "truthy", "empty"] = "eq"
    value: Any = None


class SettingDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    value_type: Literal["string", "integer", "number", "boolean", "enum", "secret", "path"]
    default: Any = None
    section: SettingSection
    level: SettingLevel = SettingLevel.BASIC
    order: int
    label_key: str
    help_key: str | None = None
    placeholder_key: str | None = None
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    secret: bool = False
    local_only: bool = False
    apply_policy: ApplyPolicy = ApplyPolicy.NEXT_TASK
    visible_when: tuple[VisibilityRule, ...] = ()
    format: Literal["url"] | None = None
    empty_as_none: bool = False
    span: Literal[1, 2] = 1

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"empty_as_none"})
        if self.secret:
            data["default"] = ""
        return data


class SectionDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: SettingSection
    label_key: str
    order: int


def _when(key: str, value: Any) -> tuple[VisibilityRule, ...]:
    return (VisibilityRule(key=key, value=value),)


SECTION_DEFINITIONS: tuple[SectionDefinition, ...] = (
    SectionDefinition(id=SettingSection.TRANSCRIPTION, label_key="settingsSectionTranscription", order=10),
    SectionDefinition(id=SettingSection.TRANSLATION, label_key="settingsSectionTranslation", order=20),
    SectionDefinition(id=SettingSection.DUBBING, label_key="settingsSectionDubbing", order=30),
    SectionDefinition(id=SettingSection.VIDEO, label_key="settingsSectionVideo", order=40),
    SectionDefinition(id=SettingSection.SYSTEM, label_key="settingsSectionSystem", order=50),
)


SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    SettingDefinition(key="ASR_MODEL_PATH", value_type="path", default="", section=SettingSection.TRANSCRIPTION, order=10, label_key="asrModelPath", placeholder_key="asrModelPathPlaceholder", local_only=True, empty_as_none=True, span=2),
    SettingDefinition(key="ASR_API_URL", value_type="string", default="", section=SettingSection.TRANSCRIPTION, order=20, label_key="asrApiUrl", placeholder_key="asrApiUrlPlaceholder", format="url", empty_as_none=True, span=2),
    SettingDefinition(key="ASR_REMOTE_MODEL", value_type="string", default="", section=SettingSection.TRANSCRIPTION, order=30, label_key="asrRemoteModel", placeholder_key="asrRemoteModelPlaceholder", empty_as_none=True, span=2),
    SettingDefinition(key="ASR_CHUNK_LENGTH", value_type="integer", default=6, section=SettingSection.TRANSCRIPTION, level=SettingLevel.ADVANCED, order=40, label_key="asrChunkLength", minimum=1),
    SettingDefinition(key="DEVICE_PREFERENCE", value_type="enum", default="auto", section=SettingSection.TRANSCRIPTION, order=50, label_key="devicePreference", choices=("auto", "gpu", "cpu")),
    SettingDefinition(key="COMPUTE_TYPE", value_type="enum", default="auto", section=SettingSection.TRANSCRIPTION, level=SettingLevel.ADVANCED, order=60, label_key="computeType", choices=("auto", "float16", "int8")),

    SettingDefinition(key="LLM_PROVIDER", value_type="enum", default="lm_studio", section=SettingSection.TRANSLATION, order=10, label_key="llmProvider", choices=("lm_studio", "ollama", "openai_compatible"), span=2),
    SettingDefinition(key="LM_STUDIO_BASE_URL", value_type="string", default="http://127.0.0.1:1234/v1", section=SettingSection.TRANSLATION, order=20, label_key="lmStudioBaseUrl", format="url", visible_when=_when("LLM_PROVIDER", "lm_studio"), span=2),
    SettingDefinition(key="LM_STUDIO_MODEL", value_type="string", default="", section=SettingSection.TRANSLATION, order=30, label_key="lmStudioModel", visible_when=_when("LLM_PROVIDER", "lm_studio"), span=2),
    SettingDefinition(key="LM_STUDIO_CLI_PATH", value_type="path", default="lms", section=SettingSection.TRANSLATION, level=SettingLevel.ADVANCED, order=40, label_key="lmStudioCliPath", local_only=True, visible_when=_when("LLM_PROVIDER", "lm_studio"), span=2),
    SettingDefinition(key="LM_STUDIO_PORT", value_type="integer", default=1234, section=SettingSection.TRANSLATION, level=SettingLevel.ADVANCED, order=50, label_key="lmStudioPort", minimum=1, maximum=65535, visible_when=_when("LLM_PROVIDER", "lm_studio")),
    SettingDefinition(key="LM_STUDIO_TTL_SECONDS", value_type="integer", default=300, section=SettingSection.TRANSLATION, level=SettingLevel.ADVANCED, order=60, label_key="lmStudioTtl", minimum=1, visible_when=_when("LLM_PROVIDER", "lm_studio")),
    SettingDefinition(key="OLLAMA_BASE_URL", value_type="string", default="http://127.0.0.1:11434", section=SettingSection.TRANSLATION, order=70, label_key="ollamaBaseUrl", format="url", visible_when=_when("LLM_PROVIDER", "ollama"), span=2),
    SettingDefinition(key="OLLAMA_MODEL", value_type="string", default="", section=SettingSection.TRANSLATION, order=80, label_key="ollamaModel", visible_when=_when("LLM_PROVIDER", "ollama"), span=2),
    SettingDefinition(key="OLLAMA_KEEP_ALIVE", value_type="string", default="5m", section=SettingSection.TRANSLATION, level=SettingLevel.ADVANCED, order=90, label_key="ollamaKeepAlive", help_key="ollamaKeepAliveHint", visible_when=_when("LLM_PROVIDER", "ollama"), span=2),
    SettingDefinition(key="LLM_API_BASE_URL", value_type="string", default="http://127.0.0.1:8001/v1", section=SettingSection.TRANSLATION, order=100, label_key="llmApiBaseUrl", format="url", visible_when=_when("LLM_PROVIDER", "openai_compatible"), span=2),
    SettingDefinition(key="LLM_API_KEY", value_type="secret", default="", section=SettingSection.TRANSLATION, order=110, label_key="llmApiKey", secret=True, visible_when=_when("LLM_PROVIDER", "openai_compatible"), span=2),
    SettingDefinition(key="LLM_MODEL_NAME", value_type="string", default="", section=SettingSection.TRANSLATION, order=120, label_key="llmModelName", visible_when=_when("LLM_PROVIDER", "openai_compatible"), span=2),
    SettingDefinition(key="LLM_TEMPERATURE", value_type="number", default=0.3, section=SettingSection.TRANSLATION, level=SettingLevel.ADVANCED, order=130, label_key="llmTemperature", minimum=0, maximum=2),
    SettingDefinition(key="TRANSLATION_BATCH_SIZE", value_type="integer", default=10, section=SettingSection.TRANSLATION, level=SettingLevel.ADVANCED, order=140, label_key="translationBatchSize", minimum=1),

    SettingDefinition(key="TTS_MODE", value_type="enum", default="kokoro", section=SettingSection.DUBBING, order=10, label_key="ttsMode", help_key="ttsModeHint", choices=("kokoro", "edge", "qwen"), span=2),
    SettingDefinition(key="QWEN_AUTO_CPU_FALLBACK", value_type="boolean", default=True, section=SettingSection.DUBBING, order=15, label_key="qwenAutoCpuFallback", help_key="qwenAutoCpuFallbackHint", visible_when=_when("TTS_MODE", "qwen")),
    SettingDefinition(key="TTS_API_URL", value_type="string", default="http://localhost:8000", section=SettingSection.DUBBING, order=20, label_key="ttsApiUrl", format="url", visible_when=_when("TTS_MODE", "speaches"), span=2),
    SettingDefinition(key="TTS_API_KEY", value_type="secret", default="", section=SettingSection.DUBBING, order=30, label_key="ttsApiKey", secret=True, visible_when=_when("TTS_MODE", "speaches"), span=2),
    SettingDefinition(key="TTS_MODEL", value_type="string", default="speaches-ai/Kokoro-82M-v1.0-ONNX", section=SettingSection.DUBBING, order=40, label_key="ttsModel", visible_when=_when("TTS_MODE", "speaches"), span=2),
    SettingDefinition(key="TTS_DEFAULT_VOICE", value_type="string", default="af_heart", section=SettingSection.DUBBING, order=50, label_key="ttsDefaultVoice"),
    SettingDefinition(key="TTS_SPEED", value_type="number", default=1.0, section=SettingSection.DUBBING, order=60, label_key="ttsSpeed", minimum=0.25, maximum=4.0),
    SettingDefinition(key="DUB_SAMPLE_RATE", value_type="integer", default=22050, section=SettingSection.DUBBING, level=SettingLevel.ADVANCED, order=70, label_key="dubSampleRate", minimum=8000, maximum=48000),
    SettingDefinition(key="KOKORO_MODEL_PATH", value_type="path", default="", section=SettingSection.DUBBING, level=SettingLevel.ADVANCED, order=80, label_key="kokoroModelPath", help_key="kokoroPathHint", local_only=True, visible_when=_when("TTS_MODE", "kokoro"), span=2),
    SettingDefinition(key="KOKORO_VOICES_PATH", value_type="path", default="", section=SettingSection.DUBBING, level=SettingLevel.ADVANCED, order=90, label_key="kokoroVoicesPath", local_only=True, visible_when=_when("TTS_MODE", "kokoro"), span=2),

    SettingDefinition(key="CHUNKING_THRESHOLD_MINUTES", value_type="integer", default=15, section=SettingSection.VIDEO, level=SettingLevel.ADVANCED, order=10, label_key="chunkingThreshold", minimum=1),
    SettingDefinition(key="FFMPEG_PATH", value_type="path", default="", section=SettingSection.VIDEO, order=20, label_key="ffmpegPath", placeholder_key="ffmpegPathPlaceholder", local_only=True, empty_as_none=True, span=2),
    SettingDefinition(key="UI_LANGUAGE", value_type="enum", default="", section=SettingSection.SYSTEM, order=10, label_key="language", choices=("", "en", "zh"), local_only=True, apply_policy="immediate"),
)


SETTINGS_BY_KEY: dict[str, SettingDefinition] = {item.key: item for item in SETTING_DEFINITIONS}
EDITABLE_SETTING_KEYS: tuple[str, ...] = tuple(item.key for item in SETTING_DEFINITIONS)


def normalize_setting_value(definition: SettingDefinition, value: Any) -> Any:
    """Normalize one submitted UI value without mutating application settings."""
    if definition.secret and value == MASKED_SECRET:
        return MASKED_SECRET
    if definition.empty_as_none and value == "":
        return None

    try:
        if definition.value_type == "integer":
            normalized: Any = int(value)
        elif definition.value_type == "number":
            normalized = float(value)
        elif definition.value_type == "boolean":
            if isinstance(value, str):
                lowered = value.lower().strip()
                if lowered not in ("true", "false"):
                    raise ValueError
                normalized = lowered == "true"
            else:
                normalized = bool(value)
        else:
            normalized = "" if value is None else str(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Field '{definition.key}' has an invalid {definition.value_type} value.") from exc

    if definition.key == "TTS_MODE" and normalized.lower().strip() == "local":
        normalized = "kokoro"
    if definition.value_type == "enum":
        normalized = normalized.lower().strip()
        if normalized not in definition.choices:
            choices = ", ".join(definition.choices)
            raise ValueError(f"Field '{definition.key}' must be one of: {choices}.")
    if definition.minimum is not None and normalized < definition.minimum:
        raise ValueError(f"Field '{definition.key}' must be >= {definition.minimum:g}.")
    if definition.maximum is not None and normalized > definition.maximum:
        raise ValueError(f"Field '{definition.key}' must be <= {definition.maximum:g}.")
    return normalized


def get_settings_schema(*, is_local: bool) -> dict[str, Any]:
    fields = [item for item in SETTING_DEFINITIONS if is_local or not item.local_only]
    used_sections = {item.section for item in fields}
    sections = [
        item.model_dump(mode="json")
        for item in SECTION_DEFINITIONS
        if item.id in used_sections
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "sections": sections,
        "fields": [item.public_dict() for item in fields],
    }


def validate_registry() -> None:
    keys = [item.key for item in SETTING_DEFINITIONS]
    if len(keys) != len(set(keys)):
        raise RuntimeError("Duplicate setting key in registry.")
    known = set(keys)
    virtual_keys = {"ASR_MODE"}
    for item in SETTING_DEFINITIONS:
        normalize_setting_value(item, item.default)
        for rule in item.visible_when:
            if rule.key not in known | virtual_keys:
                raise RuntimeError(f"Unknown visibility dependency '{rule.key}' for '{item.key}'.")


validate_registry()
