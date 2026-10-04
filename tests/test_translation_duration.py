import unittest
from unittest.mock import AsyncMock, patch

from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.translation_service import TranslationService


class TranslationDurationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        audit = patch.object(TranslationService, "_audit_batch_alignment", new_callable=AsyncMock, return_value=[])
        audit.start()
        self.addCleanup(audit.stop)

    def test_japanese_budget_uses_slot_duration(self):
        service = TranslationService()
        segment = TranscriptionSegment(start=2.0, end=8.0, text="source", confidence=1.0)

        self.assertEqual(service._dubbing_character_budget(segment, "Japanese"), 36)

    def test_korean_budget_matches_natural_edge_speech(self):
        service = TranslationService()
        segment = TranscriptionSegment(start=150.0, end=152.0, text="source", confidence=1.0)

        self.assertEqual(service._dubbing_character_budget(segment, "Korean"), 10)

    def test_duration_fitted_prompt_contains_semantics_first_pacing_targets(self):
        service = TranslationService()
        segments = [
            TranscriptionSegment(start=0.0, end=6.0, text="first", confidence=1.0),
            TranscriptionSegment(start=6.0, end=10.0, text="second", confidence=1.0),
        ]

        prompt = service._build_prompt(
            segments, [], [], "Japanese", fit_to_duration=True
        )

        self.assertIn("[36, 24]", prompt)
        self.assertIn("pacing targets", prompt)
        self.assertIn("not permission to omit meaning", prompt)

    def test_japanese_spoken_length_excludes_terminal_punctuation(self):
        self.assertEqual(
            TranslationService._dubbing_spoken_length("休みます。", "Japanese"), 4
        )

    def test_structured_output_parser_accepts_markdown_fence(self):
        self.assertEqual(
            TranslationService._parse_json_object(
                '```json\n{"translation": "ここで休みます。"}\n```'
            ),
            {"translation": "ここで休みます。"},
        )

    async def test_over_budget_translation_is_compressed_before_result(self):
        service = TranslationService()
        source = TranscriptionResult(
            video_source="fixture.mp4",
            language="English",
            segments=[TranscriptionSegment(start=0.0, end=4.0, text="long source", confidence=1.0)],
        )
        compression_calls = []

        async def translate_batch(*_args, **_kwargs):
            return ["長" * 40]

        async def compress(text, language, budget, source_text=""):
            compression_calls.append((text, language, budget, source_text))
            return "短い自然な訳です。"

        service.translate_batch = translate_batch
        service._compress_for_dubbing = compress

        result = await service.translate_full_result(
            source, "Japanese", batch_size=1, fit_to_duration=True
        )

        self.assertEqual(result.segments[0].text, "短い自然な訳です。")
        self.assertEqual(compression_calls[0][1:], ("Japanese", 24, "long source"))

    async def test_uncompressible_dubbing_translation_reaches_real_synthesis_measurement(self):
        service = TranslationService()
        source = TranscriptionResult(
            video_source="fixture.mp4",
            language="English",
            segments=[TranscriptionSegment(start=0.0, end=2.0, text="source", confidence=1.0)],
        )

        async def translate_batch(*_args, **_kwargs): return ["長" * 30]
        async def no_compression(text, *_args, **_kwargs): return text
        service.translate_batch = translate_batch
        service._compress_for_dubbing = no_compression

        result = await service.translate_full_result(
            source, "Japanese", batch_size=1, fit_to_duration=True
        )

        self.assertEqual(result.segments[0].text, "長" * 30)

    async def test_small_residual_overage_reaches_real_synthesis_measurement(self):
        service = TranslationService()
        source = TranscriptionResult(
            video_source="fixture.mp4",
            language="English",
            segments=[TranscriptionSegment(start=0.0, end=3.0, text="source", confidence=1.0)],
        )

        async def translate_batch(*_args, **_kwargs): return ["中" * 17]
        async def no_shorter(text, *_args, **_kwargs): return text
        service.translate_batch = translate_batch
        service._compress_for_dubbing = no_shorter

        result = await service.translate_full_result(
            source, "Chinese", batch_size=1, fit_to_duration=True
        )

        self.assertEqual(result.segments[0].text, "中" * 17)

    async def test_empty_batch_entry_is_retried_individually(self):
        service = TranslationService()
        source = TranscriptionResult(
            video_source="fixture.mp4",
            language="English",
            segments=[
                TranscriptionSegment(start=0.0, end=2.0, text="first", confidence=1.0),
                TranscriptionSegment(start=2.0, end=4.0, text="second", confidence=1.0),
            ],
        )
        calls = []

        async def translate_batch(current, *_args, **_kwargs):
            calls.append([segment.text for segment in current])
            return ["첫째", ""] if len(current) == 2 else ["둘째"]

        service.translate_batch = translate_batch
        result = await service.translate_full_result(
            source, "Korean", batch_size=2, fit_to_duration=True
        )

        self.assertEqual([segment.text for segment in result.segments], ["첫째", "둘째"])
        self.assertEqual(calls, [["first", "second"], ["second"]])

    async def test_terminal_punctuation_does_not_trigger_compression(self):
        service = TranslationService()
        source = TranscriptionResult(
            video_source="fixture.mp4",
            language="English",
            segments=[TranscriptionSegment(start=0.0, end=2.0, text="source", confidence=1.0)],
        )

        async def translate_batch(*_args, **_kwargs): return ["日" * 12 + "。"]
        async def unexpected_compression(*_args):
            self.fail("punctuation-only overflow must not request compression")
        service.translate_batch = translate_batch
        service._compress_for_dubbing = unexpected_compression

        result = await service.translate_full_result(
            source, "Japanese", batch_size=1, fit_to_duration=True
        )

        self.assertEqual(result.segments[0].text, "日" * 12 + "。")

    async def test_lm_studio_compression_disables_reasoning_and_parses_fence(self):
        calls = []

        class Response:
            def raise_for_status(self): pass
            def json(self):
                return {
                    "choices": [{"message": {"content": '```json\n{"translation":"短い訳。"}\n```'}}]
                }

        class Client:
            def __init__(self, **_kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def post(self, _url, **kwargs):
                calls.append(kwargs)
                return Response()

        with patch("app.services.translation_service.settings.LLM_PROVIDER", "lm_studio"):
            service = TranslationService(http_client_factory=Client)
            result = await service._compress_for_dubbing("長い翻訳です。", "Japanese", 6)

        self.assertEqual(result, "短い訳。")
        self.assertEqual(calls[0]["json"]["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()
