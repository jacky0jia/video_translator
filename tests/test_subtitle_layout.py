import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core.config import settings
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.formatter_service import SubtitleFormatter


TEXT = "这就像是一个巨大的求和芯片，分布在一个挤满人的房间里，每个人同时完成一小部分工作。AI 的大部分算术运算也可以以同样的方式拆分。"


def result(text):
    return TranscriptionResult(video_source="fixture.mp4", language="Chinese", segments=[
        TranscriptionSegment(start=0, end=2, text=text, confidence=1),
    ])


class SubtitleLayoutTests(unittest.TestCase):
    def test_single_language_leaves_line_breaks_to_renderer(self):
        ass = SubtitleFormatter().to_ass(result(TEXT), show_source=False, font_size=18, playres_x=1920, playres_y=1080)
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        self.assertIn("WrapStyle: 1", ass)
        self.assertIn(TEXT, dialogue)
        self.assertNotIn(r"\N", dialogue)

    def test_bilingual_and_extra_languages_share_a_stacked_block(self):
        ass = SubtitleFormatter().to_ass(result("译文"), original_result=result("Source"), extra_results=[result("追加訳")])
        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(dialogues), 1)
        self.assertEqual(dialogues[0].count(r"\N"), 2)
        self.assertLess(dialogues[0].index("Source"), dialogues[0].index("译文"))
        self.assertLess(dialogues[0].index("译文"), dialogues[0].index("追加訳"))

    def test_hidden_target_is_not_in_source_only_output(self):
        ass = SubtitleFormatter().to_ass(result("译文"), original_result=result("Source"), show_target=False)
        self.assertIn("Source", ass)
        self.assertNotIn("译文", ass)

    def test_bundled_libass_wraps_long_unspaced_chinese_inside_video(self):
        text = "这是一段没有任何空格而且明显超过普通字幕长度的中文测试文字用来确认自动换行始终有效这是一段没有任何空格而且明显超过普通字幕长度的中文测试文字用来确认自动换行始终有效"
        width, height = 854, 480
        ass = SubtitleFormatter().to_ass(result(text), show_source=False, font_size=18, playres_x=width, playres_y=height)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "fixture.ass").write_text(ass, encoding="utf-8")
            frame = subprocess.run([
                settings.ffmpeg_path, "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}",
                "-vf", "subtitles=fixture.ass:wrap_unicode=1", "-frames:v", "1", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
            ], cwd=directory, check=True, capture_output=True, timeout=30).stdout
        pixels = [index // 3 for index in range(0, len(frame), 3)
                  if frame[index] > 120 and frame[index + 1] > 120 and frame[index + 2] < 100]
        self.assertTrue(pixels, "Subtitle glyphs were not rendered")
        xs = [pixel % width for pixel in pixels]
        ys = sorted(set(pixel // width for pixel in pixels))
        line_bands = 1 + sum(right - left > 5 for left, right in zip(ys, ys[1:]))
        self.assertGreaterEqual(line_bands, 2)
        self.assertGreater(min(xs), 20)
        self.assertLess(max(xs), width - 20)
