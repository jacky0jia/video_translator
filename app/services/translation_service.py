import json
import logging
import re
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import httpx

from app.core.config import settings
from app.core.provider_lifecycle import NoopProviderLifecycle, ProviderLifecycle, provider_stage
from app.core.schemas import TranscriptionResult, TranscriptionSegment

logger = logging.getLogger(__name__)

_TRANSLATION_FAILURE_MARKERS = {"Translation error", "[Translation failed]"}


@dataclass(frozen=True)
class TranslationProviderConfig:
    provider: str
    api_url: str
    api_key: str
    model: str
    temperature: float
    timeout_seconds: float


class TranslationService:
    def __init__(
        self,
        *,
        http_client_factory: Callable = httpx.AsyncClient,
        lifecycle: ProviderLifecycle | None = None,
        provider_name: str | None = None,
    ):
        self._http_client_factory = http_client_factory
        self._lifecycle = lifecycle or NoopProviderLifecycle()
        self.provider_name = provider_name
        self._task_config: ContextVar[TranslationProviderConfig | None] = ContextVar(
            f"translation_provider_config_{id(self)}", default=None
        )

    @staticmethod
    def _response_format(provider: str, *, key: str, item_count: int | None = None) -> dict:
        """Build a structured-output request accepted by the selected provider.

        LM Studio 0.4 no longer accepts OpenAI's ``json_object`` mode and
        requires either ``json_schema`` or ``text``.  A schema also lets us
        require one translated string per subtitle segment.
        """
        if provider != "lm_studio":
            return {"type": "json_object"}

        value_schema: dict
        if item_count is None:
            value_schema = {"type": "string"}
        else:
            value_schema = {"type": "array", "items": {"type": "string"}}
            value_schema.update({"minItems": item_count, "maxItems": item_count})
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"subtitle_{key}",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {key: value_schema},
                    "required": [key],
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _auth_headers(api_key: str) -> dict[str, str]:
        key = str(api_key or "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    @staticmethod
    def _parse_json_object(content: object) -> dict:
        """Parse structured output even when a provider adds Markdown or prose."""
        if isinstance(content, dict):
            return content
        text = str(content or "").strip()
        candidates = [text]
        if text.startswith("```") and text.endswith("```"):
            fenced = text[3:-3].strip()
            if fenced.lower().startswith("json"):
                fenced = fenced[4:].lstrip()
            candidates.append(fenced)
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            candidates.append(text[first_brace : last_brace + 1])
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("Translation provider did not return a JSON object")

    @staticmethod
    def _current_config() -> TranslationProviderConfig:
        """Snapshot live settings without requiring an application restart."""
        base_url = str(settings.LLM_API_BASE_URL or "").rstrip("/")
        provider = settings.LLM_PROVIDER
        if provider == "ollama":
            base_url = str(settings.OLLAMA_BASE_URL or "http://127.0.0.1:11434").rstrip("/") + "/v1"
        elif provider == "lm_studio":
            base_url = str(settings.LM_STUDIO_BASE_URL or "http://127.0.0.1:1234/v1").rstrip("/")
        return TranslationProviderConfig(
            provider=provider,
            api_url=f"{base_url}/chat/completions",
            api_key=settings.LLM_API_KEY,
            model=(settings.OLLAMA_MODEL or settings.LLM_MODEL_NAME) if provider == "ollama"
            else settings.LM_STUDIO_MODEL if provider == "lm_studio"
            else settings.LLM_MODEL_NAME,
            temperature=settings.LLM_TEMPERATURE,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        )

    async def translate_batch(
        self,
        current_batch: List[TranscriptionSegment],
        prev_context: List[TranscriptionSegment] = [],
        next_context: List[TranscriptionSegment] = [],
        target_lang: str = "Chinese",
        fit_to_duration: bool = False,
        strict_length: bool = False,
        existing_translations: List[str] | None = None,
    ) -> List[str]:
        """
        Translates a batch of segments using a sliding window approach for context.
        Returns a list of translated strings.
        """
        import asyncio

        # 1. Build the prompt with contextual references
        prompt = self._build_prompt(
            current_batch, prev_context, next_context, target_lang,
            fit_to_duration=fit_to_duration,
            strict_length=strict_length,
            existing_translations=existing_translations,
        )
        provider_config = self._task_config.get() or self._current_config()
        # Structured output constrains the shape, but not the length of each
        # string. Bound generation so one pathological batch cannot occupy the
        # only GPU until every HTTP retry times out.
        max_output_tokens = max(256, min(len(current_batch) * 160, 2048))

        max_retries = settings.LLM_MAX_RETRIES
        for attempt in range(max_retries + 1):
            try:
                async with self._http_client_factory(timeout=provider_config.timeout_seconds, verify=settings.VERIFY_SSL) as client:
                    request_body = {
                        "model": provider_config.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a professional video subtitle translator. Your goal is to provide natural translations while maintaining the original tone and context. Always respond in strict JSON format.",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": self._response_format(
                            provider_config.provider,
                            key="translations",
                            item_count=len(current_batch),
                        ),
                        "max_tokens": max_output_tokens,
                        "temperature": provider_config.temperature,
                    }
                    if provider_config.provider == "lm_studio":
                        # Translation is a direct transformation task. Gemma 4
                        # otherwise defaults to reasoning and can consume the
                        # full output budget before writing message.content.
                        request_body["reasoning_effort"] = "none"
                    response = await client.post(
                        provider_config.api_url,
                        headers=self._auth_headers(provider_config.api_key),
                        json=request_body,
                    )
                    response.raise_for_status()
                    data = response.json()

                    # Parse the content
                    content = data["choices"][0]["message"]["content"]
                    result = self._parse_json_object(content)

                    # The LLM should return a list of translations under a key like "translations"
                    translations = result.get("translations", [])

                    if len(translations) != len(current_batch):
                        logger.warning(
                            "LLM returned %s translations, but expected %s. "
                            "The caller will retry this batch segment by segment.",
                            len(translations),
                            len(current_batch),
                        )

                    return translations

            except Exception as e:
                logger.error(
                    f"Translation API error ({type(e).__name__}) on attempt {attempt + 1}/{max_retries + 1}: {e}"
                )
                if attempt < max_retries:
                    wait = settings.LLM_RETRY_BACKOFF_BASE * (attempt + 1)
                    logger.info(f"Retrying batch in {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    return ["Translation error" for _ in current_batch]

    async def _audit_batch_alignment(
        self, segments: List[TranscriptionSegment], translations: List[str],
    ) -> List[int]:
        """Identify translations that clearly contain adjacent-row meaning."""
        if len(segments) != len(translations):
            return list(range(len(segments)))
        provider_config = self._task_config.get() or self._current_config()
        pairs = "\n".join(
            f"{index}. SOURCE: {segment.text}\n   TRANSLATION: {translation}"
            for index, (segment, translation) in enumerate(zip(segments, translations))
        )
        prompt = (
            "Audit source-to-translation alignment for timed video subtitles. "
            "Return only zero-based indices whose translation clearly contains meaning from "
            "an adjacent source row, omits the row's meaning, or belongs to a different row. "
            "A source fragment may have an incomplete translation fragment; that is valid. "
            "Be conservative and do not flag stylistic differences. Do not rewrite text. "
            'Respond as JSON: {"misaligned_indices": []}.\n' + pairs
        )
        if provider_config.provider == "lm_studio":
            response_format = {"type": "json_schema", "json_schema": {
                "name": "subtitle_alignment", "strict": True,
                "schema": {"type": "object", "properties": {
                    "misaligned_indices": {"type": "array", "items": {"type": "integer"}}
                }, "required": ["misaligned_indices"], "additionalProperties": False},
            }}
        else:
            response_format = {"type": "json_object"}
        async with self._http_client_factory(
            timeout=provider_config.timeout_seconds, verify=settings.VERIFY_SSL,
        ) as client:
            response = await client.post(
                provider_config.api_url,
                headers=self._auth_headers(provider_config.api_key),
                json={"model": provider_config.model, "messages": [
                    {"role": "system", "content": "You are a strict subtitle alignment auditor. Return JSON only."},
                    {"role": "user", "content": prompt},
                ], "response_format": response_format, "max_tokens": 256, "temperature": 0,
                    **({"reasoning_effort": "none"} if provider_config.provider == "lm_studio" else {})},
            )
            response.raise_for_status()
            result = self._parse_json_object(response.json()["choices"][0]["message"]["content"])
        indices = result.get("misaligned_indices")
        if not isinstance(indices, list) or any(
            isinstance(index, bool) or not isinstance(index, int)
            or index < 0 or index >= len(segments) for index in indices
        ):
            raise ValueError("Translation alignment audit returned invalid row indices")
        return sorted(set(indices))

    def _build_prompt(
        self,
        current_batch: List[TranscriptionSegment],
        prev_context: List[TranscriptionSegment],
        next_context: List[TranscriptionSegment],
        target_lang: str,
        fit_to_duration: bool = False,
        strict_length: bool = False,
        existing_translations: List[str] | None = None,
    ) -> str:
        """
        Constructs the sliding window prompt.
        """

        # Format segments for the prompt
        def fmt(seg):
            return f"[{seg.start:.2f}s -> {seg.end:.2f}s]: {seg.text}"

        prev_text = "\n".join([fmt(s) for s in prev_context])
        curr_text = "\n".join([f"{i}. {fmt(s)}" for i, s in enumerate(current_batch)])
        next_text = "\n".join([fmt(s) for s in next_context])

        dubbing_requirements = ""
        if fit_to_duration:
            budgets = [self._dubbing_character_budget(seg, target_lang) for seg in current_batch]
            dubbing_requirements = (
                "4. These translations will be spoken within the original time slots. "
                "First be accurate, then concise and clear: express the full meaning in natural spoken "
                "language with no unnecessary filler or repetition, while preserving every "
                "fact, name, number, qualification, negation, and causal relationship from the source.\n"
                f"5. Preferred character targets for translations 0..{len(budgets) - 1}: {budgets}. "
                "These are pacing targets, not permission to omit meaning. If a target cannot be met without "
                "changing or weakening the meaning, preserve the meaning and exceed the target.\n"
            )
            if str(target_lang).strip().lower() in {"japanese", "ja", "jp"}:
                dubbing_requirements += (
                    f"Japanese pacing targets: {budgets}. 自然で簡潔な話し言葉にしてください。"
                    "ただし、意味・固有名詞・数値・否定・条件・因果関係を省略しないでください。\n"
                )
            if strict_length:
                dubbing_requirements += (
                    "6. A previous result missed its pacing target. Rephrase it more compactly without "
                    "summarizing away information. Preserve meaning rather than forcing the character target.\n"
                )
            if existing_translations is not None:
                current_text = "\n".join(f"{i}. {value}" for i, value in enumerate(existing_translations))
                dubbing_requirements += (
                    "The following existing translations were measured as too long for speech. "
                    "Rewrite each one more compactly, aiming for 15-25% fewer characters. "
                    "Keep the original meaning as faithfully as possible: do not remove or alter any "
                    "fact, name, number, negation, condition, qualification, or causal relationship. "
                    "If a row cannot be shortened safely, return it unchanged; never invent or omit information.\n"
                    f"Existing translations:\n{current_text}\n"
                )

        prompt = (
            f"Translate the following video segments into {target_lang}. \n\n"
            f"### Contextual Reference (Do NOT translate these):\n"
            f"Previous: \n{prev_text if prev_text else 'None'}\n\n"
            f"Next: \n{next_text if next_text else 'None'}\n\n"
            f"### Target Segments to Translate:\n"
            f"{curr_text}\n\n"
            f"### Requirements:\n"
            f"1. Translate accurately, concisely, and clearly while maintaining the original tone and emotional context. Preserve all facts, numbers, names, negations, conditions, and causal relationships.\n"
            f"2. Output only a JSON object with a 'translations' key containing a list of strings in the same order as the target segments.\n"
            f"3. You MUST return EXACTLY {len(current_batch)} translations, one for each target segment. Do NOT merge multiple segments into one translation, even if a segment is very short.\n"
            "4. Keep each translation aligned to its own source row; do not borrow meaning from an adjacent row.\n"
            f"{dubbing_requirements}"
            f'Example: {{"translations": ["翻译1", "翻译2"]}}\n'
        )
        return prompt

    @staticmethod
    def _dubbing_character_budget(segment: TranscriptionSegment, target_lang: str) -> int:
        """Conservative normal-speech text budget for a subtitle time slot."""
        normalized = str(target_lang or "").strip().lower()
        chars_per_second = {
            "japanese": 6.0, "ja": 6.0, "jp": 6.0,
            "korean": 5.0, "ko": 5.0,
            "chinese": 5.0, "zh": 5.0,
            "english": 14.0, "en": 14.0,
        }.get(normalized, 8.0)
        duration = max(float(segment.end) - float(segment.start), 0.5)
        return max(int(duration * chars_per_second), 4)

    @staticmethod
    def _dubbing_spoken_length(text: str, target_lang: str) -> int:
        """Estimate spoken units without charging CJK punctuation as syllables."""
        normalized = str(target_lang or "").strip().lower()
        if normalized not in {
            "japanese", "ja", "jp", "korean", "ko", "chinese", "zh",
        }:
            return len(str(text or "").strip())
        return sum(
            1
            for character in str(text or "")
            if not unicodedata.category(character).startswith(("P", "Z"))
        )

    @staticmethod
    def _translation_failed(value: object) -> bool:
        text = str(value or "").strip()
        return not text or text in _TRANSLATION_FAILURE_MARKERS

    @staticmethod
    def _safe_shorter_translation(previous: str, proposed: str) -> bool:
        before, after = previous.strip(), proposed.strip()
        if not before or not after or len(after) >= len(before):
            return False
        protected = re.findall(r"\d+(?:[.,]\d+)?|\b[A-Z]{2,}\b", before)
        if any(token not in after for token in protected):
            return False
        negations = ("不", "没", "未", "无", "非", "not", "never", "without")
        if any(marker in before.lower() for marker in negations) and not any(
            marker in after.lower() for marker in negations
        ):
            return False
        logical_markers = (
            (("如果", "若", "if"), ("如果", "若", "if")),
            (("除非", "unless"), ("除非", "unless")),
            (("因为", "由于", "because"), ("因为", "由于", "因", "because")),
        )
        for originals, equivalents in logical_markers:
            if any(marker in before.lower() for marker in originals) and not any(
                marker in after.lower() for marker in equivalents
            ):
                return False
        return True

    async def compress_for_dubbing(
        self, source: TranscriptionResult, translated: TranscriptionResult,
        segment_indices: List[int], target_lang: str, task_id: str,
    ) -> tuple[TranscriptionResult, List[int]]:
        """After explicit approval, shorten only measured overlong translations."""
        if len(source.segments) != len(translated.segments):
            raise ValueError("转录与译文的字幕行数不一致，无法安全压缩")
        selected = sorted(set(segment_indices))
        if not selected or any(index < 0 or index >= len(source.segments) for index in selected):
            raise ValueError("没有可安全压缩的字幕行")
        rewritten = list(translated.segments)
        changed: List[int] = []
        config_token = None
        try:
            async with provider_stage(
                self._lifecycle, task_id=task_id, stage="translation",
                provider=self.provider_name or settings.LLM_PROVIDER,
            ):
                config_token = self._task_config.set(self._current_config())
                for index in selected:
                    previous = translated.segments[index].text
                    candidates = await self.translate_batch(
                        [source.segments[index]],
                        source.segments[max(0, index - 2):index],
                        source.segments[index + 1:index + 3], target_lang,
                        fit_to_duration=True, strict_length=True,
                        existing_translations=[previous],
                    )
                    if len(candidates) != 1 or not self._safe_shorter_translation(previous, candidates[0]):
                        continue
                    rewritten[index] = translated.segments[index].model_copy(update={"text": candidates[0].strip()})
                    changed.append(index)
        finally:
            if config_token is not None:
                self._task_config.reset(config_token)
        if not changed:
            raise ValueError("未找到既能缩短又能保留关键信息的译文，原译文已保留")
        return translated.model_copy(update={"segments": rewritten}), changed

    async def _compress_for_dubbing(
        self,
        translation: str,
        target_lang: str,
        max_characters: int,
        source_text: str = "",
    ) -> str:
        """Rephrase a translation toward a pacing target without dropping meaning."""
        provider_config = self._task_config.get() or self._current_config()
        prompt = (
            f"Rewrite the text below as concise, natural spoken {target_lang}. "
            f"Aim for at most {max_characters} visible characters including punctuation. "
            "Preserve every fact, name, number, qualification, negation, and causal relationship. "
            "Do not summarize, generalize, introduce ambiguity, or omit details. If the target cannot be met "
            "without changing meaning, return the shortest faithful wording even if it exceeds the target. "
            "Return JSON only: {\"translation\": \"...\"}.\n\n"
            f"Source meaning reference: {source_text or '(unavailable)'}\n"
            f"Translation to rephrase: {translation}"
        )
        if str(target_lang).strip().lower() in {"japanese", "ja", "jp"}:
            prompt += (
                f"\n必ず{max_characters}文字以内の自然な日本語に要約してください。"
                "文字数を超える説明は削除し、文を途中で切らないでください。"
            )
        try:
            async with self._http_client_factory(
                timeout=provider_config.timeout_seconds, verify=settings.VERIFY_SSL
            ) as client:
                request_body = {
                    "model": provider_config.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You rewrite dubbing scripts to strict character limits and return strict JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": self._response_format(
                        provider_config.provider,
                        key="translation",
                    ),
                    "max_tokens": 256,
                    "temperature": 0,
                }
                if provider_config.provider == "lm_studio":
                    request_body["reasoning_effort"] = "none"
                response = await client.post(
                    provider_config.api_url,
                    headers=self._auth_headers(provider_config.api_key),
                    json=request_body,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                candidate = str(
                    self._parse_json_object(content).get("translation", "")
                ).strip()
                return candidate or translation
        except Exception as exc:
            logger.warning("Dubbing translation compression failed: %s", exc)
            return translation

    async def translate_full_result(
        self,
        result: TranscriptionResult,
        target_lang: str = "Chinese",
        batch_size: int = None,
        progress_callback=None,
        task_id: str | None = None,
        fit_to_duration: bool = False,
    ) -> TranscriptionResult:
        """
        Orchestrates the sliding window translation process for the entire result.
        """
        if batch_size is None:
            batch_size = settings.TRANSLATION_BATCH_SIZE
        all_segments = result.segments
        total = len(all_segments)
        translated_texts = []
        alignment_review_rows: set[int] = set()
        alignment_check_incomplete = False
        total_batches = (total + batch_size - 1) // batch_size
        provider_name = settings.LLM_PROVIDER
        config_token = None
        try:
            async with provider_stage(
                self._lifecycle,
                task_id=task_id,
                stage="translation",
                provider=self.provider_name or provider_name,
            ):
                provider_config = self._current_config()
                config_token = self._task_config.set(provider_config)
                logger.info(
                    "Translation task %s uses model '%s' at %s",
                    task_id or "standalone",
                    provider_config.model,
                    provider_config.api_url,
                )
                for batch_idx, i in enumerate(range(0, total, batch_size)):
            # Define window
                    current_batch = all_segments[i : i + batch_size]
                    prev_context = all_segments[max(0, i - 2) : i]
                    next_context = all_segments[i + batch_size : min(i + batch_size + 2, total)]

                    logger.info(
                        f"Translating batch {i // batch_size + 1} (segments {i} to {min(i + batch_size, total)})"
                    )
                    batch_translations = await self.translate_batch(
                        current_batch, prev_context, next_context, target_lang,
                        **({"fit_to_duration": True} if fit_to_duration else {}),
                    )

            # If LLM merged or split segments, retry segment by segment for this batch
                    if len(batch_translations) != len(current_batch):
                        logger.warning(
                            f"Batch {batch_idx + 1} returned {len(batch_translations)} translations but expected {len(current_batch)}. Retrying segment by segment..."
                        )
                        batch_translations = []
                        for seg in current_batch:
                            single = await self.translate_batch(
                                [seg], [], [], target_lang,
                                **({"fit_to_duration": True} if fit_to_duration else {}),
                            )
                            if len(single) == 1:
                                batch_translations.append(single[0])
                            else:
                                logger.error(
                                    "Single-segment translation returned %s results; "
                                    "using a failure marker without shifting later rows.",
                                    len(single),
                                )
                                batch_translations.append("[Translation failed]")

                    # Structured output can contain the correct number of items
                    # while one entry is empty. Recover only those rows so a
                    # transient generation miss does not discard a valid batch.
                    for local_index, translation in enumerate(batch_translations):
                        if not self._translation_failed(translation):
                            continue
                        logger.warning(
                            "Translation for segment %s is empty or invalid; retrying individually",
                            i + local_index + 1,
                        )
                        for _ in range(2):
                            single = await self.translate_batch(
                                [current_batch[local_index]], [], [], target_lang,
                                **(
                                    {"fit_to_duration": True, "strict_length": True}
                                    if fit_to_duration else {}
                                ),
                            )
                            if len(single) == 1 and not self._translation_failed(single[0]):
                                batch_translations[local_index] = single[0]
                                break

                    if not any(self._translation_failed(value) for value in batch_translations):
                        try:
                            suspect_indices = await self._audit_batch_alignment(current_batch, batch_translations)
                        except Exception as exc:
                            logger.warning("Translation alignment audit unavailable for batch %s: %s", batch_idx + 1, exc)
                            alignment_check_incomplete = True
                            suspect_indices = []
                        if suspect_indices:
                            for local_index in suspect_indices:
                                row = i + local_index
                                try:
                                    candidate = await self.translate_batch(
                                        [current_batch[local_index]],
                                        all_segments[max(0, row - 2):row],
                                        all_segments[row + 1:row + 3],
                                        target_lang, fit_to_duration=fit_to_duration,
                                    )
                                    if len(candidate) == 1 and not self._translation_failed(candidate[0]):
                                        batch_translations[local_index] = candidate[0]
                                except Exception as exc:
                                    logger.warning("Alignment retry for row %s failed: %s", row + 1, exc)
                            try:
                                unresolved = await self._audit_batch_alignment(current_batch, batch_translations)
                            except Exception as exc:
                                logger.warning("Alignment recheck for batch %s failed: %s", batch_idx + 1, exc)
                                alignment_check_incomplete = True
                                unresolved = suspect_indices
                            alignment_review_rows.update(i + index + 1 for index in unresolved)

                    # Character budgets are only a prompt hint. Do not rewrite a
                    # valid translation before measuring the actual TTS audio.

                    translated_texts.extend(batch_translations)

                    if progress_callback:
                        await progress_callback(batch_idx + 1, total_batches)
        finally:
            if config_token is not None:
                self._task_config.reset(config_token)

        # Create new segments with translated text
        new_segments = []
        for i, original in enumerate(all_segments):
            new_segments.append(
                TranscriptionSegment(
                    start=original.start,
                    end=original.end,
                    text=translated_texts[i]
                    if i < len(translated_texts)
                    else original.text,
                    confidence=original.confidence,
                )
            )

        return TranscriptionResult(
            video_source=result.video_source,
            language=target_lang,
            segments=new_segments,
            alignment_review_rows=sorted(alignment_review_rows),
            alignment_check_incomplete=alignment_check_incomplete,
        )


from app.core.ollama_lifecycle import translation_provider_lifecycle

translation_service = TranslationService(
    lifecycle=translation_provider_lifecycle,
)
