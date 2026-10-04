import unittest
import tempfile
import subprocess
from pathlib import Path

from app.services.video_burn_service import END_NOTE_URL, VideoBurnService


class VideoBurnRateControlTests(unittest.TestCase):
    def test_nvenc_uses_quality_target_instead_of_source_bitrate(self):
        args = VideoBurnService._video_encode_args("h264_nvenc", 1_000_000)

        self.assertEqual(args[args.index("-b:v") + 1], "0")
        self.assertEqual(args[args.index("-cq") + 1], "18")
        self.assertEqual(args[args.index("-preset") + 1], "p7")
        self.assertEqual(args[args.index("-multipass") + 1], "fullres")
        self.assertEqual(args[args.index("-rc-lookahead") + 1], "32")

    def test_software_fallback_does_not_inherit_low_av1_bitrate(self):
        args = VideoBurnService._video_encode_args(None, 2_000_000)

        self.assertEqual(args[:4], ["-c:v", "libx264", "-preset", "slow"])
        self.assertEqual(args[args.index("-crf") + 1], "18")
        self.assertNotIn("-b:v", args)

    def test_unknown_bitrate_uses_reasonable_quality_fallback(self):
        args = VideoBurnService._video_encode_args(None, None)

        self.assertEqual(args, ["-c:v", "libx264", "-preset", "slow", "-crf", "18"])

    def test_source_color_metadata_is_forwarded(self):
        args = VideoBurnService._video_output_args({"color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"})
        self.assertIn("passthrough", args)
        self.assertEqual(args[args.index("-colorspace") + 1], "bt709")

    def test_end_note_is_visual_only_and_uses_last_three_seconds(self):
        service = VideoBurnService()
        with tempfile.TemporaryDirectory() as directory:
            path, duration = service._generate_ass(
                {"task_id": "note"}, False, False, {}, Path(directory),
                playres_x=1920, playres_y=1080,
                end_note_enabled=True, video_duration=10.0, dubbed=True,
            )
            content = path.read_text(encoding="utf-8-sig")
        self.assertEqual(duration, 10.0)
        self.assertIn("Style: EndNote", content)
        self.assertIn("Dialogue: 1,0:00:07.00,0:00:10.00,EndNote", content)
        self.assertIn("AI-assisted translation and dubbing with Video Translator", content)
        self.assertIn(END_NOTE_URL, content)
        self.assertEqual(content.count("Dialogue:"), 1)
        self.assertNotIn("[Audio]", content)

    def test_subtitle_video_note_does_not_claim_dubbing(self):
        content = VideoBurnService._append_end_note("[Events]", 2.0, 640, 360, False)
        self.assertIn("AI-assisted translation with Video Translator", content)
        self.assertNotIn("translation and dubbing", content)
        self.assertIn("Dialogue: 1,0:00:00.00,0:00:02.00", content)

    def test_note_only_ass_renders_with_software_ffmpeg(self):
        service = VideoBurnService()
        if not Path(service.ffmpeg_path).is_file():
            self.skipTest("FFmpeg is not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ass_path, _ = service._generate_ass(
                {"task_id": "smoke"}, False, False, {}, root,
                playres_x=640, playres_y=360, end_note_enabled=True,
                video_duration=1.0, dubbed=True,
            )
            output_path = root / "note.mp4"
            process = subprocess.run(
                [service.ffmpeg_path, "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=5:d=1",
                 "-vf", f"ass={ass_path.name}", "-c:v", "libx264", "-preset", "ultrafast",
                 "-an", str(output_path)],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr[-1000:])
            self.assertGreater(output_path.stat().st_size, 0)
            profile = service._probe_video_profile(output_path)
            self.assertTrue(profile["probed"])
            self.assertAlmostEqual(profile["duration"], 1.0, delta=0.2)
            decoded = subprocess.run(
                [service.ffmpeg_path, "-v", "error", "-i", str(output_path),
                 "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                capture_output=True,
            )
            self.assertEqual(decoded.returncode, 0)
            self.assertGreater(len(set(decoded.stdout)), 1, "End note was not drawn on the frame")


if __name__ == "__main__":
    unittest.main()
