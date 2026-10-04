import unittest

from app.services.video_burn_service import VideoBurnService


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


if __name__ == "__main__":
    unittest.main()
