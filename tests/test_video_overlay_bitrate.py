from unittest.mock import patch

import pytest

from app.services.video_output import make_output_plan


SOURCE = dict(probed=True, codec="h264", width=640, height=360,
              bitrate=4757, duration=12, frame_rate="25/1")


def choose(_ffmpeg, profile, **kwargs):
    return dict(codec=profile["codec"], encoder="h264_nvenc", software=False,
                bitrate=profile["bitrate"])


@pytest.mark.parametrize("policy", ["balanced", "resolution", "codec"])
def test_static_source_has_enough_bitrate_for_new_subtitles(policy):
    with patch("app.services.video_output.select_encoder", side_effect=choose):
        plan = make_output_plan("ffmpeg", SOURCE, {"policy": policy})
    assert plan["bitrate"] == 500_000
    assert plan["overlay_bitrate_floor_applied"] is True
    assert (plan["width"], plan["height"]) == (640, 360)


def test_custom_low_bitrate_is_respected():
    with patch("app.services.video_output.select_encoder", side_effect=choose):
        plan = make_output_plan("ffmpeg", SOURCE, {"policy": "custom", "bitrate_mbps": 0.1})
    assert plan["bitrate"] == 100_000
    assert plan["overlay_bitrate_floor_applied"] is False


def test_stream_copy_without_overlays_keeps_original_bitrate():
    with patch("app.services.video_output.select_encoder") as select:
        plan = make_output_plan("ffmpeg", SOURCE, overlays=False)
    assert plan["bitrate"] == 4757
    assert plan["encoder"] == "copy"
    select.assert_not_called()


def test_normal_source_bitrate_is_unchanged():
    with patch("app.services.video_output.select_encoder", side_effect=choose):
        plan = make_output_plan("ffmpeg", {**SOURCE, "bitrate": 2_000_000})
    assert plan["bitrate"] == 2_000_000
    assert plan["overlay_bitrate_floor_applied"] is False
