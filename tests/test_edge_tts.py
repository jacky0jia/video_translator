import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.edge_tts_service import EdgeTTSService
from app.services.dubbing_service import DubbingCancelled, DubbingService


class Communicator:
    def __init__(self, chunks=None, error=None):
        self.chunks, self.error = chunks or [], error

    async def stream(self):
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk


class EdgeTTSTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_catalog_includes_all_supported_languages(self):
        async def list_voices():
            return [
                {"ShortName": "zh-CN-XiaoxiaoNeural", "Locale": "zh-CN", "Gender": "Female"},
                {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female"},
                {"ShortName": "ja-JP-NanamiNeural", "Locale": "ja-JP", "Gender": "Female"},
                {"ShortName": "ko-KR-SunHiNeural", "Locale": "ko-KR", "Gender": "Female"},
                {"ShortName": "fr-FR-DeniseNeural", "Locale": "fr-FR", "Gender": "Female"},
            ]

        EdgeTTSService._voice_cache = None
        with patch.dict("sys.modules", {"edge_tts": SimpleNamespace(list_voices=list_voices)}):
            voices = await EdgeTTSService("ffmpeg").list_voices()
        self.assertEqual({voice["language"] for voice in voices}, {"zh", "en", "ja", "ko"})
        self.assertNotIn("fr-FR-DeniseNeural", {voice["id"] for voice in voices})

    def test_language_routing_never_silently_changes_provider(self):
        with patch("app.services.dubbing_service.settings.TTS_MODE", "kokoro"):
            self.assertEqual(DubbingService.provider_for_language("Chinese"), "kokoro")
            self.assertEqual(DubbingService.provider_for_language("ko"), "kokoro")
        with patch("app.services.dubbing_service.settings.TTS_MODE", "edge"):
            self.assertEqual(DubbingService.provider_for_language("ko"), "edge")

    def test_dubbing_cancel_signal_is_observed(self):
        import threading
        service = DubbingService(edge_provider=object())
        service._cancel_events["task"] = threading.Event()
        self.assertTrue(service.request_cancel("task"))
        with self.assertRaises(DubbingCancelled):
            service._check_cancelled("task")

    async def test_synthesis_converts_audio_and_maps_speed(self):
        calls = []

        def factory(text, voice, rate):
            calls.append((text, voice, rate))
            return Communicator([{"type": "audio", "data": b"mp3"}])

        service = EdgeTTSService("ffmpeg", communicator_factory=factory)
        with patch.object(service, "_mp3_to_wav", return_value=b"RIFF-wave") as convert:
            result = await service.synthesize("안녕하세요", "ko-KR-SunHiNeural", 1.2)
        self.assertEqual(result, b"RIFF-wave")
        self.assertEqual(calls, [("안녕하세요", "ko-KR-SunHiNeural", "+20%")])
        convert.assert_called_once_with(b"mp3")

    async def test_network_failure_retries_and_has_clear_error(self):
        attempts = 0

        def factory(*args):
            nonlocal attempts
            attempts += 1
            return Communicator(error=OSError("offline"))

        service = EdgeTTSService("ffmpeg", communicator_factory=factory, retries=2)
        with patch("app.services.edge_tts_service.asyncio.sleep", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "internet connection"):
                await service.synthesize("text", "ko-KR-SunHiNeural", 1)
        self.assertEqual(attempts, 3)

    def test_ffmpeg_conversion_uses_project_wav_shape(self):
        service = EdgeTTSService("ffmpeg")
        wav = bytearray(b"RIFF" + b"\xff\xff\xff\xff" + b"WAVEfmt " + b"\x10\x00\x00\x00" + b"\x00" * 16 + b"data" + b"\xff\xff\xff\xff" + b"\x00" * 20)
        completed = type("Result", (), {"returncode": 0, "stdout": bytes(wav), "stderr": b""})()
        with patch("app.services.edge_tts_service.subprocess.run", return_value=completed) as run:
            converted = service._mp3_to_wav(b"mp3")
        self.assertEqual(int.from_bytes(converted[4:8], "little"), len(converted) - 8)
        data_tag = converted.find(b"data")
        self.assertEqual(int.from_bytes(converted[data_tag + 4:data_tag + 8], "little"), 20)
        command = run.call_args.args[0]
        self.assertIn("22050", command)
        self.assertIn("1", command)


if __name__ == "__main__": unittest.main()
