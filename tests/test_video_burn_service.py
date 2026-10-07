import unittest
import tempfile
import subprocess
import json
import io
from unittest.mock import patch
from pathlib import Path

from app.services.video_burn_service import END_NOTE_URL, VideoBurnService
from app.services.video_encoding import encode_args, select_encoder


class VideoBurnRateControlTests(unittest.TestCase):
    def test_nvenc_uses_source_bitrate_budget(self):
        args = encode_args("av1_nvenc", 12_000_000)
        self.assertEqual(args[args.index("-b:v") + 1], "12000000")
        self.assertEqual(args[args.index("-maxrate") + 1], "15000000")
        self.assertEqual(args[args.index("-preset") + 1], "p5")
        self.assertEqual(args[args.index("-multipass") + 1], "fullres")

    def test_software_av1_keeps_codec_and_bitrate(self):
        args = encode_args('libaom-av1', 2_000_000)
        self.assertEqual(args[:2], ['-c:v', 'libaom-av1'])
        self.assertEqual(args[args.index('-b:v') + 1], '2000000')
        self.assertNotIn('-crf', args)

    def test_unknown_bitrate_uses_reasonable_quality_fallback(self):
        args = encode_args('libx264')
        self.assertEqual(args, ['-c:v', 'libx264', '-preset', 'medium', '-crf', '23'])

    def test_mkv_missing_stream_bitrate_uses_container_estimate(self):
        service = VideoBurnService()
        data = {'streams': [{'codec_name': 'av1', 'width': 3840, 'height': 2160}],
                'format': {'duration': '874.448', 'bit_rate': '12282261'}}
        with patch('app.services.video_burn_service.subprocess.run') as run:
            run.return_value.stdout = json.dumps(data)
            profile = service._probe_video_profile(Path('input.mkv'))
        self.assertEqual(profile['bitrate'], 12282261)
        self.assertEqual(profile['bitrate_source'], 'container_estimate')

    def test_stream_tag_bitrate_takes_precedence_over_container(self):
        data = {'streams': [{'codec_name': 'hevc', 'width': 1920, 'height': 1080,
                             'tags': {'BPS': '4000000'}}],
                'format': {'duration': '10', 'bit_rate': '4500000'}}
        with patch('app.services.video_burn_service.subprocess.run') as run:
            run.return_value.stdout = json.dumps(data)
            profile = VideoBurnService()._probe_video_profile(Path('input.mkv'))
        self.assertEqual(profile['bitrate'], 4000000)
        self.assertEqual(profile['bitrate_source'], 'stream_tags')

    def test_hardware_list_is_not_treated_as_hardware_support(self):
        profile = {'probed': True, 'codec': 'av1', 'width': 3840, 'height': 2160, 'bitrate': 12000000}
        with patch('app.services.video_encoding.subprocess.run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, stdout=' V....D av1_nvenc\n V....D libaom-av1\n'),
                               subprocess.CompletedProcess([], 1)]
            plan = select_encoder('ffmpeg', profile)
        self.assertEqual(plan['encoder'], 'libaom-av1')
        self.assertTrue(plan['software'])
        self.assertIn('color=s=3840x2160:r=1', run.call_args.args[0])

    def test_software_retry_never_crosses_codecs(self):
        for codec, encoder in [('av1', 'libaom-av1'), ('hevc', 'libx265'), ('h264', 'libx264')]:
            with self.subTest(codec=codec), patch('app.services.video_encoding.subprocess.run') as run:
                run.return_value.stdout = '\n'.join(' V....D ' + e for e in ['av1_nvenc', 'libaom-av1', 'libx264', 'libx265'])
                plan = select_encoder('ffmpeg', {'probed': True, 'codec': codec}, software_only=True)
                self.assertEqual(plan['encoder'], encoder)
                run.assert_called_once()

    def test_unavailable_av1_never_silently_becomes_h264(self):
        with patch('app.services.video_encoding.subprocess.run') as run:
            run.return_value.stdout = ' V....D libx264\n'
            with self.assertRaisesRegex(RuntimeError, 'No usable AV1 encoder'):
                select_encoder('ffmpeg', {'probed': True, 'codec': 'av1'})

    def test_unknown_codec_requires_explicit_action(self):
        with self.assertRaisesRegex(RuntimeError, 'Cannot preserve source video codec'):
            select_encoder('ffmpeg', {'probed': False, 'codec': 'unknown'})

    def test_mid_job_hardware_failure_retries_same_codec_and_notifies_cpu(self):
        service = VideoBurnService()
        profile = {'probed': True, 'codec': 'av1', 'width': 640, 'height': 360,
                   'duration': 1, 'bitrate': 2000000}
        hardware = {'codec': 'av1', 'encoder': 'av1_nvenc', 'software': False, 'bitrate': 2000000}
        software = {**hardware, 'encoder': 'libaom-av1', 'software': True}
        processes = [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        from unittest.mock import Mock
        processes = [Mock(returncode=p.returncode, stderr=io.BytesIO(b'')) for p in processes]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.mp4'
            source.write_bytes(b'test')
            task = {'task_id': 'render-test', 'filename': 'input.mkv', 'upload_path': str(source)}
            with (patch('app.services.video_burn_service.history_manager') as history,
                  patch.object(service, '_probe_video_profile', return_value=profile),
                  patch.object(service, '_generate_ass', return_value=(root / 'note.ass', 1)),
                  patch('app.services.video_burn_service.select_encoder', side_effect=[hardware, software]),
                  patch('app.services.video_burn_service.subprocess.Popen', side_effect=processes) as popen,
                  patch('app.services.video_burn_service.emit_event') as event,
                  patch('app.services.video_burn_service.settings.OUTPUT_DIR', root / 'output'),
                  patch('app.services.video_burn_service.settings.TEMP_DIR', root / 'temp')):
                history.get_task.return_value = task
                result = service.burn('render-test', False, False, {}, end_note_enabled=True)
        self.assertTrue(result.endswith('.mp4'))
        commands = [call.args[0] for call in popen.call_args_list]
        self.assertEqual([cmd[cmd.index('-c:v') + 1] for cmd in commands], ['av1_nvenc', 'libaom-av1'])
        self.assertTrue(any(call.args[1].get('message') == 'software_encoding' for call in event.call_args_list))

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


    def test_av1_render_preserves_codec_dimensions_and_frame_rate(self):
        service = VideoBurnService()
        if not Path(service.ffmpeg_path).is_file():
            self.skipTest('FFmpeg is not installed')
        encoders = subprocess.run([service.ffmpeg_path, '-hide_banner', '-encoders'],
                                  capture_output=True, text=True, check=True).stdout
        if 'libaom-av1' not in encoders:
            self.skipTest('FFmpeg has no libaom AV1 encoder')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ass, _ = service._generate_ass({'task_id': 'av1-smoke'}, False, False, {}, root,
                playres_x=320, playres_y=180, end_note_enabled=True, video_duration=1, dubbed=True)
            output = root / 'av1.mp4'
            command = [service.ffmpeg_path, '-hide_banner', '-y', '-f', 'lavfi', '-i',
                       'testsrc2=s=320x180:r=10:d=1', '-vf', f'ass={ass.name}',
                       *encode_args('libaom-av1', 300000), '-an', str(output)]
            process = subprocess.run(command, cwd=root, capture_output=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr[-1000:])
            profile = service._probe_video_profile(output)
            self.assertEqual(profile['codec'], 'av1')
            self.assertEqual((profile['width'], profile['height']), (320, 180))
            self.assertEqual(profile['frame_rate'], '10/1')
            self.assertLess(output.stat().st_size, 150000)
            decoded = subprocess.run([service.ffmpeg_path, '-v', 'error', '-i', str(output),
                                      '-f', 'null', '-'], capture_output=True, timeout=30)
            self.assertEqual(decoded.returncode, 0, decoded.stderr[-1000:])


if __name__ == "__main__":
    unittest.main()
