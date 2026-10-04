import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.services.dubbing_service import DubbingCompressionRequired, DubbingService
from app.services.tts.kokoro import KokoroTTSProvider


def segment(start: float, end: float, text: str = "Sentence."):
    return SimpleNamespace(start=start, end=end, text=text)


class DubbingTimelineTests(unittest.TestCase):
    def setUp(self):
        self.service = object.__new__(DubbingService)
        self.service.ffmpeg_path = "ffmpeg"

    def test_overflow_requests_approval_at_provider_speed_cap(self):
        self.service._probe_duration = lambda _: 100.0
        for provider in ("qwen", "edge"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(DubbingCompressionRequired) as raised:
                    self.service._build_timeline(
                        [segment(0, 10)], [Path("raw.wav")], 10,
                        Path(directory), provider=provider,
                    )
                message = str(raised.exception)
                self.assertIn("确认前不会修改字幕", message)
                if provider == "qwen":
                    self.assertIn("1.60x", message)
                else:
                    self.assertIn("1.40x", message)

    def test_timeline_adds_leading_internal_and_trailing_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            silences = []
            normalized = []

            def write_silence(path, seconds):
                silences.append((path.name, seconds))

            def normalize_segment(raw, out_path, tempo_ratio):
                normalized.append((raw.name, tempo_ratio))
                return out_path

            self.service._write_silence = write_silence
            self.service._normalize_natural_audio = normalize_segment
            self.service._probe_duration = lambda _path: 1.0
            self.service._run_ffmpeg = lambda *_args: None

            self.service._build_timeline(
                [segment(1.0, 2.0), segment(3.0, 4.0)],
                [Path("raw-1.wav"), Path("raw-2.wav")],
                6.0,
                temp_dir,
            )

            self.assertEqual(normalized, [("raw-1.wav", 1.0), ("raw-2.wav", 1.0)])
            self.assertEqual(
                silences,
                [("gap_000000.wav", 1.0), ("gap_000001.wav", 1.0), ("gap_trailing.wav", 2.0)],
            )

    def test_natural_timeline_delays_next_segment_instead_of_overlapping(self):
        with tempfile.TemporaryDirectory() as directory:
            tempos = []
            silences = []
            self.service._write_silence = (
                lambda _path, seconds: silences.append(seconds)
            )
            self.service._normalize_natural_audio = (
                lambda _raw, out_path, tempo: tempos.append(tempo) or out_path
            )
            self.service._probe_duration = lambda _path: 1.5
            self.service._run_ffmpeg = lambda *_args: None

            self.service._build_timeline(
                [segment(0.0, 2.0), segment(1.5, 3.0)],
                [Path("raw-1.wav"), Path("raw-2.wav")],
                3.0,
                Path(directory),
            )

            self.assertEqual(len(tempos), 2)
            self.assertAlmostEqual(tempos[0], tempos[1])
            self.assertLessEqual(tempos[0], 1.12)
            self.assertTrue(any(gap >= 0.239 for gap in silences))

    def test_timeline_uses_one_tempo_for_all_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            tempos = []
            durations = {"raw-1.wav": 1.0, "raw-2.wav": 2.0}
            self.service._probe_duration = lambda path: durations[path.name]
            self.service._write_silence = lambda *_args: None
            self.service._normalize_natural_audio = (
                lambda _raw, out_path, tempo: tempos.append(tempo) or out_path
            )
            self.service._run_ffmpeg = lambda *_args: None

            self.service._build_timeline(
                [segment(0.0, 1.0), segment(1.0, 2.0)],
                [Path("raw-1.wav"), Path("raw-2.wav")],
                4.0,
                Path(directory),
            )

            self.assertEqual(tempos, [1.0, 1.0])

    def test_impossible_local_sync_requests_compression_instead_of_rushing_a_row(self):
        with tempfile.TemporaryDirectory() as directory:
            tempos = []
            silences = []
            self.service._probe_duration = lambda _path: 5.6
            self.service._write_silence = lambda _path, seconds: silences.append(seconds)
            self.service._normalize_natural_audio = (
                lambda _raw, out_path, tempo: tempos.append(tempo) or out_path
            )
            self.service._run_ffmpeg = lambda *_args: None

            with self.assertRaises(DubbingCompressionRequired):
                self.service._build_timeline(
                    [segment(0.0, 1.0), segment(0.0, 1.0)],
                    [Path("raw-1.wav"), Path("raw-2.wav")],
                    10.25, Path(directory), provider="edge",
                )
            self.assertEqual(tempos, [])

    def test_final_utterance_can_use_preceding_silence_to_avoid_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            tempos = []
            silences = []
            durations = {"raw-1.wav": 1.0, "raw-2.wav": 2.0}
            self.service._probe_duration = lambda path: durations[path.name]
            self.service._write_silence = lambda _path, seconds: silences.append(seconds)
            self.service._normalize_natural_audio = (
                lambda _raw, out_path, tempo: tempos.append(tempo) or out_path
            )
            self.service._run_ffmpeg = lambda *_args: None

            self.service._build_timeline(
                [segment(0.0, 1.0), segment(4.0, 5.0)],
                [Path("raw-1.wav"), Path("raw-2.wav")],
                5.0,
                Path(directory),
                provider="edge",
            )

            self.assertEqual(len(tempos), 2)
            self.assertAlmostEqual(tempos[0], tempos[1])
            self.assertLessEqual(tempos[0], 1.40)

    def test_adjustable_provider_has_small_tempo_margin_beyond_qwen(self):
        with tempfile.TemporaryDirectory() as directory:
            tempos = []
            self.service._probe_duration = lambda _path: 11.1
            self.service._write_silence = lambda *_args: None
            self.service._normalize_natural_audio = (
                lambda _raw, out_path, tempo: tempos.append(tempo) or out_path
            )
            self.service._run_ffmpeg = lambda *_args: None

            self.service._build_timeline(
                [segment(0.0, 10.0)], [Path("raw.wav")], 10.0,
                Path(directory), provider="edge",
            )

            self.assertGreater(tempos[0], 1.1)
            self.assertLessEqual(tempos[0], 1.12)

    def test_tempo_uses_robust_percentile_instead_of_longest_outlier(self):
        ratios = [1.0, 1.05, 1.1, 1.15, 5.0]

        selected = self.service._percentile(ratios, 0.75)

        self.assertAlmostEqual(selected, 1.15)

    def test_slot_can_borrow_gap_but_never_cross_next_segment(self):
        segments = [segment(0.0, 1.0), segment(2.0, 3.0)]

        self.assertEqual(self.service._timeline_slot_end(segments, 0, 4.0), 1.5)
        self.assertEqual(self.service._timeline_slot_end(segments, 1, 4.0), 3.5)

    def test_contiguous_segments_keep_a_small_audible_gap(self):
        segments = [segment(0.0, 1.0), segment(1.0, 2.0)]

        self.assertAlmostEqual(
            self.service._timeline_slot_end(segments, 0, 2.0), 0.92
        )

    def test_overlong_segment_gets_local_speedup_before_trimming(self):
        commands = []
        self.service._probe_duration = lambda _path: 1.4
        self.service._run_ffmpeg = lambda command, _label: commands.append(command)

        self.service._fit_segment_audio(
            Path("raw.wav"), 1.0, Path("fitted.wav"), tempo_ratio=1.1
        )

        filter_chain = commands[0][commands[0].index("-af") + 1]
        self.assertIn("atempo=1.4000", filter_chain)
        self.assertNotIn("afade=", filter_chain)

    def test_unavoidable_trim_uses_fade_out(self):
        commands = []
        self.service._probe_duration = lambda _path: 2.0
        self.service._run_ffmpeg = lambda command, _label: commands.append(command)

        self.service._fit_segment_audio(
            Path("raw.wav"), 1.0, Path("fitted.wav"), tempo_ratio=1.2
        )

        filter_chain = commands[0][commands[0].index("-af") + 1]
        self.assertIn("atempo=1.5000", filter_chain)
        self.assertIn("afade=t=out:st=0.9600:d=0.0400", filter_chain)

    def test_fragments_are_grouped_until_sentence_end(self):
        grouped = self.service._group_segments_for_dubbing([
            segment(0.0, 1.0, "这是"),
            segment(1.0, 2.0, "一个完整句子。"),
            segment(2.1, 3.0, "下一句。"),
        ])

        self.assertEqual(len(grouped), 2)
        self.assertEqual(grouped[0].text, "这是一个完整句子。")
        self.assertEqual((grouped[0].start, grouped[0].end), (0.0, 2.0))

    def test_distributed_lead_absorbs_small_accumulated_overrun(self):
        segments = [segment(1.0, 2.0), segment(3.0, 4.0), segment(5.0, 6.0)]
        durations = [1.4, 1.4, 1.4]

        without_lead = self.service._natural_timeline_end(
            segments, durations, 1.0, 0.32, 6.1, 0.0
        )
        with_lead = self.service._natural_timeline_end(
            segments, durations, 1.0, 0.32, 6.1, 0.4
        )

        self.assertGreater(without_lead, 6.1)
        self.assertLessEqual(with_lead, 6.1)

    def test_outer_silence_trim_keeps_internal_pauses(self):
        commands = []
        self.service._run_ffmpeg = lambda command, _label: commands.append(command)

        self.service._trim_outer_silence(Path("raw.wav"), Path("prepared.wav"))

        filter_chain = commands[0][commands[0].index("-af") + 1]
        self.assertEqual(filter_chain.count("start_periods=1"), 2)
        self.assertEqual(filter_chain.count("areverse"), 2)
        self.assertNotIn("stop_periods", filter_chain)
        self.assertIn("start_threshold=-60dB", filter_chain)
        self.assertIn("start_silence=0.04", filter_chain)
        self.assertIn("start_silence=0.08", filter_chain)

    def test_chinese_fragments_join_without_artificial_space(self):
        grouped = self.service._group_segments_for_dubbing([
            segment(0.0, 1.0, "今天我们讨论"),
            segment(1.0, 2.0, "智能代理的长期记忆。"),
        ])

        self.assertEqual(grouped[0].text, "今天我们讨论智能代理的长期记忆。")

    def test_mixed_latin_and_chinese_fragments_keep_word_boundary(self):
        self.assertEqual(
            self.service._join_dubbing_fragments(["使用 LangGraph", "构建记忆系统。"]),
            "使用 LangGraph 构建记忆系统。",
        )

    def test_ellipsis_does_not_force_fragment_boundary(self):
        grouped = self.service._group_segments_for_dubbing([
            segment(0.0, 1.0, "一个例子是..."),
            segment(1.0, 2.0, "助手。"),
        ])

        self.assertEqual([group.text for group in grouped], ["一个例子是... 助手。"])

    def test_dub_uses_video_duration_for_timeline(self):
        task = {
            "filename": "fixture.mp4",
            "upload_path": "fixture.mp4",
        }
        result = SimpleNamespace(segments=[segment(0.0, 2.0)])
        captured = {}

        self.service._verify_ffmpeg = lambda: None
        self.service._resolve_source_json = lambda *_args: (result, "English")
        self.service._probe_duration = lambda _path: 5.0
        self.service._synthesize_all = lambda *_args: _async_result([Path("raw.wav")])
        self.service._build_timeline = (
            lambda _segments, _raw, duration, _temp, **_kwargs: captured.setdefault("duration", duration)
            or Path("dub.wav")
        )
        self.service._mux_video = lambda *_args: None
        self.service._to_download_url = lambda path: str(path)

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.dubbing_service.history_manager.get_task", return_value=task
        ), patch(
            "app.services.dubbing_service.history_manager.update_task"
        ), patch(
            "app.services.dubbing_service.emit_event"
        ), patch(
            "app.services.dubbing_service.settings.TEMP_DIR", Path(directory) / "temp"
        ), patch(
            "app.services.dubbing_service.settings.OUTPUT_DIR", Path(directory) / "output"
        ), patch("pathlib.Path.exists", return_value=True), patch(
            "shutil.copyfile"
        ):
            self.service.dub("task-1", None, "af_heart", 1.0)

        self.assertEqual(captured["duration"], 5.0)

    def test_dub_does_not_extend_timeline_to_rounded_subtitle_end(self):
        task = {"filename": "fixture.mp4", "upload_path": "fixture.mp4"}
        result = SimpleNamespace(segments=[segment(0.0, 5.0)])
        captured = {}
        self.service._verify_ffmpeg = lambda: None
        self.service._resolve_source_json = lambda *_args: (result, "English")
        self.service._probe_duration = lambda _path: 3.0
        self.service._synthesize_all = lambda *_args: _async_result([Path("raw.wav")])
        self.service._build_timeline = (
            lambda _segments, _raw, duration, _temp, **_kwargs:
            captured.setdefault("duration", duration) or Path("dub.wav")
        )
        self.service._mux_video = lambda *_args: None
        self.service._to_download_url = lambda path: str(path)

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.dubbing_service.history_manager.get_task", return_value=task
        ), patch("app.services.dubbing_service.history_manager.update_task"), patch(
            "app.services.dubbing_service.emit_event"
        ), patch(
            "app.services.dubbing_service.settings.TEMP_DIR", Path(directory) / "temp"
        ), patch(
            "app.services.dubbing_service.settings.OUTPUT_DIR", Path(directory) / "output"
        ), patch("pathlib.Path.exists", return_value=True), patch("shutil.copyfile"):
            self.service.dub("task-1", None, "af_heart", 1.0)

        self.assertEqual(captured["duration"], 3.0)

    def test_explicit_target_never_falls_back_to_another_translation(self):
        self.service._load_transcription = lambda path: f"loaded:{path}"
        task = {
            "target_lang": "Chinese",
            "translation_path": "zh-main.json",
            "translations": {"Chinese": "zh.json"},
            "transcription_path": "source.json",
        }

        with self.assertRaisesRegex(ValueError, "Japanese"):
            self.service._resolve_source_json(task, "Japanese")

    def test_explicit_target_uses_matching_translation(self):
        self.service._load_transcription = lambda path: f"loaded:{path}"
        task = {
            "translations": {
                "Chinese": "zh.json",
                "Japanese": "ja.json",
            },
            "translation_path": "zh.json",
        }

        result, language = self.service._resolve_source_json(task, "Japanese")

        self.assertEqual(result, "loaded:ja.json")
        self.assertEqual(language, "Japanese")

    def test_local_tts_splits_and_retries_kokoro_phoneme_overflow(self):
        calls = []

        class Kokoro:
            def create(self, text, **_kwargs):
                calls.append(text)
                if len(text) > 40:
                    raise IndexError("index 510 is out of bounds for axis 0 with size 510")
                return np.ones(100, dtype=np.float32), 22050

        provider = KokoroTTSProvider()
        provider._get_kokoro = lambda: Kokoro()
        provider._get_ja_g2p = lambda: (lambda value: (value, None))
        text = "前半の文章です。これは音素数が多すぎる入力を安全に分割するためのテストです。後半の文章も欠落させずに合成します。"

        fake_soundfile = SimpleNamespace(
            write=lambda buffer, *_args, **_kwargs: buffer.write(b"RIFF-test")
        )
        with patch.dict("sys.modules", {"soundfile": fake_soundfile}):
            wav = provider._synthesize_local(text, "jf_alpha", 1.0)

        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertGreater(len(calls), 1)
        successful = [part for part in calls if len(part) <= 40]
        self.assertEqual("".join(successful), text)

    def test_local_tts_proactively_splits_long_input(self):
        calls = []

        class Kokoro:
            def create(self, text, **_kwargs):
                calls.append(text)
                return np.ones(100, dtype=np.float32), 22050

        provider = KokoroTTSProvider()
        provider._get_kokoro = lambda: Kokoro()
        provider._get_ja_g2p = lambda: (lambda value: (value, None))
        text = ("This is a long sentence with a natural boundary. " * 6).strip()
        fake_soundfile = SimpleNamespace(
            write=lambda buffer, *_args, **_kwargs: buffer.write(b"RIFF-test")
        )

        with patch.dict("sys.modules", {"soundfile": fake_soundfile}):
            wav = provider._synthesize_local(text, "jf_alpha", 1.0)

        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertGreater(len(calls), 1)
        self.assertEqual("".join(calls).replace(" ", ""), text.replace(" ", ""))
        self.assertTrue(all(len(part) <= 180 for part in calls))

    def test_local_tts_split_prefers_punctuation_near_middle(self):
        left, right = KokoroTTSProvider.split_text("第一部分です。第二部分です。第三部分です。")

        self.assertEqual(left + right, "第一部分です。第二部分です。第三部分です。")
        self.assertTrue(left.endswith("。"))


async def _async_result(value):
    return value


if __name__ == "__main__":
    unittest.main()
