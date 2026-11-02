import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

import httpx

from app.core.config import settings
from app.core.schemas import TranscriptionResult
from app.services.asr_service import ASRService


def long_segment():
    return SimpleNamespace(
        start=0.0, end=12.0, text=" First second. Third fourth. ", avg_logprob=-0.25,
        words=[
            SimpleNamespace(start=0.0, end=3.0, word=" First"),
            SimpleNamespace(start=3.0, end=6.0, word=" second."),
            SimpleNamespace(start=6.0, end=9.0, word=" Third"),
            SimpleNamespace(start=9.0, end=12.0, word=" fourth."),
        ],
    )


class LocalASRSegmentTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        model = Mock()
        model.transcribe.return_value = (
            iter([long_segment()]),
            SimpleNamespace(language="en", language_probability=0.99),
        )
        service = ASRService()
        service.model = model
        return service, model

    async def test_local_model_requests_fixed_context_and_word_timestamps(self):
        service, model = self.make_service()
        with patch.object(settings, "ASR_API_URL", ""), patch.object(
            settings, "SUBTITLE_SEGMENT_SECONDS", 8
        ), patch.object(settings, "ASR_BEAM_SIZE", 5):
            await service.transcribe(Path("audio.wav"), language="en")
        model.transcribe.assert_called_once_with(
            "audio.wav", language="en", beam_size=5, task="transcribe",
            chunk_length=30, word_timestamps=True,
        )
        self.assertNotIn("vad_filter", model.transcribe.call_args.kwargs)
        self.assertNotIn("hallucination_silence_threshold", model.transcribe.call_args.kwargs)

    async def test_local_rows_split_using_configured_subtitle_duration(self):
        for limit, expected in (
            (8, [(0.0, 6.0, "First second."), (6.0, 12.0, "Third fourth.")]),
            (4, [(0.0, 3.0, "First"), (3.0, 6.0, "second."),
                 (6.0, 9.0, "Third"), (9.0, 12.0, "fourth.")]),
        ):
            with self.subTest(limit=limit):
                service, _ = self.make_service()
                with patch.object(settings, "ASR_API_URL", ""), patch.object(
                    settings, "SUBTITLE_SEGMENT_SECONDS", limit
                ):
                    result = await service.transcribe(Path("audio.wav"))
                self.assertIsInstance(result, TranscriptionResult)
                self.assertEqual(result.video_source, "audio.wav")
                self.assertEqual(result.language, "en")
                self.assertEqual(
                    [(row.start, row.end, row.text) for row in result.segments], expected
                )
                self.assertTrue(all(row.confidence == -0.25 for row in result.segments))


class RemoteASRBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_request_and_long_row_remain_openai_compatible(self):
        requests = []
        payload = {
            "language": "en",
            "segments": [{"start": 0.0, "end": 12.0, "text": " First second. Third fourth. ",
                          "avg_logprob": -0.25,
                          "words": [vars(word) for word in long_segment().words]}],
        }

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, *, files, data):
                requests.append((url, data, files["file"][0], files["file"][1].read(), files["file"][2]))
                return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

        client_factory = Mock(return_value=FakeClient())
        service = ASRService(http_client_factory=client_factory)
        service._get_model = Mock(side_effect=AssertionError("Remote ASR loaded a local model"))
        with patch.object(settings, "ASR_API_URL", "https://asr.example.test/"), patch.object(
            settings, "ASR_REMOTE_MODEL", "whisper-remote"
        ), patch.object(settings, "SUBTITLE_SEGMENT_SECONDS", 8), patch.object(
            settings, "VERIFY_SSL", True
        ), patch("app.services.asr_service.open", mock_open(read_data=b"wave-data")):
            result = await service.transcribe(Path("remote.wav"), language="en")

        self.assertEqual(requests, [(
            "https://asr.example.test/v1/audio/transcriptions",
            {"model": "whisper-remote", "language": "en", "response_format": "verbose_json"},
            "remote.wav", b"wave-data", "audio/wav",
        )])
        client_factory.assert_called_once_with(timeout=300.0, verify=True)
        self.assertEqual(result.video_source, "remote.wav")
        self.assertEqual(result.language, "en")
        self.assertEqual(
            [row.model_dump() for row in result.segments],
            [{"start": 0.0, "end": 12.0, "text": "First second. Third fourth.", "confidence": -0.25, "source_text_edited": False}],
        )
