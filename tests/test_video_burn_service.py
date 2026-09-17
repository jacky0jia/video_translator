import unittest

from app.services.video_burn_service import VideoBurnService


class VideoBurnRateControlTests(unittest.TestCase):
    def test_nvenc_reserves_average_budget_and_allows_scene_change_peaks(self):
        args = VideoBurnService._video_encode_args("h264_nvenc", 1_000_000)

        self.assertEqual(args[args.index("-b:v") + 1], "920000")
        self.assertEqual(args[args.index("-maxrate") + 1], "3000000")
        self.assertEqual(args[args.index("-bufsize") + 1], "4000000")
        self.assertEqual(args[args.index("-preset") + 1], "p7")
        self.assertEqual(args[args.index("-multipass") + 1], "fullres")
        self.assertEqual(args[args.index("-profile:v") + 1], "high")
        self.assertEqual(args[args.index("-rc-lookahead") + 1], "32")
        self.assertNotIn("-cq", args)

    def test_software_fallback_targets_source_bitrate(self):
        args = VideoBurnService._video_encode_args(None, 2_000_000)

        self.assertEqual(args[:4], ["-c:v", "libx264", "-preset", "slow"])
        self.assertEqual(args[args.index("-b:v") + 1], "1840000")
        self.assertEqual(args[args.index("-maxrate") + 1], "6000000")

    def test_unknown_bitrate_uses_reasonable_quality_fallback(self):
        args = VideoBurnService._video_encode_args(None, None)

        self.assertEqual(args, ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])


if __name__ == "__main__":
    unittest.main()
