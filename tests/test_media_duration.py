import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.media_duration import PROBE_TIMEOUT_SECONDS, probe_media_duration


class MediaDurationTests(unittest.TestCase):
    def test_pcm_wav_uses_exact_header_duration_without_subprocess(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "speech.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(22050)
                audio.writeframes(b"\x00\x00" * 12345)
            with patch("app.services.media_duration.subprocess.run") as run:
                self.assertEqual(probe_media_duration(path, "ffmpeg"), 12345 / 22050)
                run.assert_not_called()

    def inspect(self, data):
        with patch("app.services.media_duration.shutil.which", return_value="ffprobe"), patch(
            "app.services.media_duration.subprocess.run",
            return_value=SimpleNamespace(stdout=json.dumps(data)),
        ) as run:
            duration = probe_media_duration(Path("fixture.mkv"), "ffmpeg")
            command = run.call_args.args[0]
            self.assertNotIn("-f", command)
            self.assertIn("-show_entries", command)
            self.assertEqual(run.call_args.kwargs["timeout"], PROBE_TIMEOUT_SECONDS)
            return duration

    def test_container_duration_and_stream_fallback(self):
        self.assertEqual(self.inspect({"format": {"duration": "874.448"}}), 874.448)
        self.assertEqual(self.inspect({"format": {"duration": "N/A"}, "streams": [
            {"duration": "12.34"}, {"duration": "13.45"}
        ]}), 13.45)

    def test_missing_probe_reads_headers_without_null_output(self):
        with patch("app.services.media_duration.Path.is_file", return_value=False), patch(
            "app.services.media_duration.shutil.which", return_value=None
        ), patch("app.services.media_duration.subprocess.run", return_value=SimpleNamespace(
            stderr="Duration: 00:14:34.45, start: 0.0"
        )) as run:
            self.assertEqual(probe_media_duration(Path("fixture.mkv"), "ffmpeg"), 874.45)
            self.assertEqual(run.call_args.args[0], ["ffmpeg", "-hide_banner", "-i", "fixture.mkv"])
            self.assertEqual(run.call_args.kwargs["timeout"], PROBE_TIMEOUT_SECONDS)

    def test_timeout_does_not_attempt_unbounded_fallback(self):
        with patch("app.services.media_duration.shutil.which", return_value="ffprobe"), patch(
            "app.services.media_duration.subprocess.run",
            side_effect=subprocess.TimeoutExpired("ffprobe", PROBE_TIMEOUT_SECONDS),
        ) as run:
            self.assertEqual(probe_media_duration(Path("fixture.mkv"), "ffmpeg"), 0)
            self.assertEqual(run.call_count, 1)

    def test_invalid_metadata_uses_only_bounded_header_fallback(self):
        for metadata in ("invalid json", "[]", '{"format":{"duration":"NaN"}}',
                         '{"format":{"duration":"Infinity"}}', '{"format":{"duration":-1}}'):
            with self.subTest(metadata=metadata), patch(
                "app.services.media_duration.shutil.which", return_value="ffprobe"
            ), patch("app.services.media_duration.subprocess.run", side_effect=[
                SimpleNamespace(stdout=metadata), SimpleNamespace(stderr="Duration: N/A")
            ]) as run:
                self.assertEqual(probe_media_duration(Path("fixture.mkv"), "ffmpeg"), 0)
                self.assertEqual(run.call_count, 2)
                self.assertNotIn("null", run.call_args.args[0])

    def test_probe_failure_and_missing_media_return_zero(self):
        with patch("app.services.media_duration.shutil.which", return_value="ffprobe"), patch(
            "app.services.media_duration.subprocess.run", side_effect=[
                subprocess.CalledProcessError(1, "ffprobe"), FileNotFoundError()
            ]
        ):
            self.assertEqual(probe_media_duration(Path("missing.mp4"), "ffmpeg"), 0)
