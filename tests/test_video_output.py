import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.tasks import VideoPlanRequest, preview_video_plan
from app.api.pipeline import PipelineRequest, CompressionDecision, decide_compression, start_pipeline
from app.services.video_output import VideoOutputOptions, fit_dimensions, make_output_plan


SOURCE = dict(probed=True, codec='av1', width=3840, height=2160,
              bitrate=12_280_000, bitrate_source='container_estimate',
              duration=874, frame_rate='60000/1001')


def fake_select(ffmpeg, profile, *, hardware_only=False, excluded_encoders=(), **kwargs):
    codec = profile['codec']
    if hardware_only:
        raise RuntimeError('No hardware')
    return dict(codec=codec, encoder={'av1': 'libaom-av1', 'h264': 'libx264', 'hevc': 'libx265'}[codec],
                software=True, bitrate=profile.get('bitrate'))


class VideoOutputTests(unittest.TestCase):
    def test_choices_reject_unsafe_values(self):
        for fields in [dict(policy='unsafe'), dict(codec='-c:v'), dict(resolution='99999'),
                       dict(bitrate_mbps=float('nan')), dict(bitrate_mbps=float('inf')),
                       dict(bitrate_mbps=0), dict(bitrate_mbps=201), dict(extra='bad')]:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                VideoOutputOptions(**fields)
        self.assertEqual(PipelineRequest(task_id='one').output_options.policy, 'balanced')

    def test_dimensions_preserve_orientation_and_never_upscale(self):
        self.assertEqual(fit_dimensions(3840, 2160, '1440'), (2560, 1440))
        self.assertEqual(fit_dimensions(2160, 3840, '1440'), (1440, 2560))
        self.assertEqual(fit_dimensions(640, 360, '1440'), (640, 360))
        self.assertEqual(fit_dimensions(4096, 2160, 'source'), (4096, 2160))
        w, h = fit_dimensions(4096, 1716, '1440')
        self.assertLessEqual(w, 2560)
        self.assertLess(abs(w / h - 4096 / 1716), 0.01)

    @patch('app.services.video_output.select_encoder', side_effect=fake_select)
    def test_av1_default_cpu_fallback_is_bounded_h264_1440p(self, select):
        plan = make_output_plan('ffmpeg', SOURCE)
        self.assertEqual((plan['codec'], plan['width'], plan['height']), ('h264', 2560, 1440))
        self.assertEqual(plan['frame_rate'], SOURCE['frame_rate'])
        self.assertLessEqual(plan['bitrate'], SOURCE['bitrate'])
        self.assertTrue(plan['software'])
        self.assertGreater(plan['estimated_bytes'], 0)

    @patch('app.services.video_output.select_encoder')
    def test_av1_hardware_preserves_source(self, select):
        select.return_value = dict(codec='av1', encoder='av1_nvenc', software=False, bitrate=SOURCE['bitrate'])
        plan = make_output_plan('ffmpeg', SOURCE)
        self.assertEqual((plan['width'], plan['height']), (3840, 2160))
        self.assertTrue(select.call_args.kwargs['hardware_only'])

    @patch('app.services.video_output.select_encoder')
    def test_prefers_hevc_hardware_when_av1_unavailable(self, select):
        select.side_effect = [RuntimeError('AV1 unavailable'), dict(codec='hevc', encoder='hevc_nvenc', software=False, bitrate=8_000_000)]
        plan = make_output_plan('ffmpeg', SOURCE)
        self.assertEqual(plan['codec'], 'hevc')
        self.assertFalse(plan['software'])
        self.assertEqual(select.call_args.args[1]['width'], 2560)

    @patch('app.services.video_output.select_encoder', side_effect=fake_select)
    def test_non_av1_retains_codec_size_rate(self, select):
        for codec in ['hevc', 'h264']:
            with self.subTest(codec=codec):
                plan = make_output_plan('ffmpeg', {**SOURCE, 'codec': codec})
                self.assertEqual(plan['codec'], codec)
                self.assertEqual(plan['bitrate'], SOURCE['bitrate'])
                self.assertEqual(plan['width'], SOURCE['width'])

    @patch('app.services.video_output.select_encoder', side_effect=fake_select)
    def test_explicit_keep_codec_allows_av1_cpu(self, select):
        plan = make_output_plan('ffmpeg', SOURCE, dict(policy='codec'))
        self.assertEqual(plan['codec'], 'av1')
        self.assertTrue(plan['software'])
        self.assertEqual(plan['height'], 2160)

    @patch('app.services.video_output.select_encoder', side_effect=fake_select)
    def test_keep_resolution_uses_alternative_without_scaling(self, select):
        plan = make_output_plan('ffmpeg', SOURCE, dict(policy='resolution'))
        self.assertEqual(plan['codec'], 'hevc')
        self.assertEqual(plan['height'], 2160)

    @patch('app.services.video_output.select_encoder', side_effect=fake_select)
    def test_custom_bitrate_resolution_and_codec(self, select):
        plan = make_output_plan('ffmpeg', SOURCE, dict(policy='custom', codec='h264', resolution='1080', bitrate_mbps=7.5))
        self.assertEqual((plan['codec'], plan['width'], plan['height'], plan['bitrate']), ('h264', 1920, 1080, 7_500_000))

    @patch('app.services.video_output.select_encoder', side_effect=RuntimeError('No usable AV1'))
    def test_custom_never_silently_changes_codec(self, select):
        with self.assertRaisesRegex(RuntimeError, 'No usable AV1'):
            make_output_plan('ffmpeg', SOURCE, dict(policy='custom', codec='av1'))
        select.assert_called_once()

    @patch('app.services.video_output.select_encoder')
    def test_no_overlay_copies_original_despite_custom_choices(self, select):
        plan = make_output_plan('ffmpeg', SOURCE, dict(policy='custom', codec='h264'), overlays=False)
        self.assertEqual(plan['encoder'], 'copy')
        self.assertEqual(plan['codec'], 'av1')
        self.assertEqual(plan['height'], 2160)
        select.assert_not_called()

    @patch('app.services.video_output.select_encoder', side_effect=fake_select)
    def test_missing_rate_gets_bounded_estimate(self, select):
        plan = make_output_plan('ffmpeg', {**SOURCE, 'codec': 'h264', 'bitrate': None})
        self.assertLessEqual(plan['bitrate'], 40_000_000)
        self.assertTrue(plan['bitrate_estimated'])

    def test_unknown_probe_cannot_claim_a_valid_plan(self):
        with self.assertRaises(ValueError):
            make_output_plan('ffmpeg', {'probed': False})

    @patch('app.services.video_output.select_encoder', side_effect=fake_select)
    def test_mid_job_replan_excludes_failed_encoder(self, select):
        make_output_plan('ffmpeg', SOURCE, excluded_encoders=('av1_nvenc',))
        self.assertTrue(all(c.kwargs['excluded_encoders'] == ('av1_nvenc',) for c in select.call_args_list))

    def test_burn_scales_before_ass_and_uses_output_playres(self):
        from app.services.video_burn_service import VideoBurnService
        service = VideoBurnService()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mkv'; source.touch()
            history = Mock()
            history.get_task.return_value = {'task_id': 'one', 'filename': source.name, 'upload_path': str(source)}
            process = Mock(returncode=0, stderr=io.BytesIO(b''))
            plan = dict(codec='h264', encoder='libx264', software=True, bitrate=8_000_000, width=2560, height=1440)
            with (patch('app.services.video_burn_service.history_manager', history),
                  patch('app.services.video_burn_service.emit_event'),
                  patch.object(service, '_probe_video_profile', side_effect=[SOURCE, {**SOURCE, 'codec': 'h264', 'width': 2560, 'height': 1440}]),
                  patch.object(service, '_generate_ass', return_value=(root / 'test.ass', 874)) as ass,
                  patch('app.services.video_burn_service.make_output_plan', return_value=plan),
                  patch('app.services.video_burn_service.subprocess.Popen', return_value=process) as popen,
                  patch('app.services.video_burn_service.settings.TEMP_DIR', root / 'temp'),
                  patch('app.services.video_burn_service.settings.OUTPUT_DIR', root / 'output')):
                service.burn('one', False, True, {}, output_options={'policy': 'balanced'})
            self.assertEqual(ass.call_args.args[6:8], (2560, 1440))
            cmd = popen.call_args.args[0]
            self.assertEqual(cmd[cmd.index('-vf') + 1], 'scale=2560:1440,ass=test.ass')
            self.assertIn('passthrough', cmd)
            self.assertNotIn('-r', cmd)


class VideoPlanApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_pipeline_forwards_choices_without_premature_persistence(self):
        request = PipelineRequest(task_id='one', output_options={'policy': 'codec'}, voice='voice')
        with (patch('app.api.pipeline.history_manager') as history,
              patch('app.api.pipeline.pipeline_service.start') as start,
              patch('app.api.pipeline.resolve_tts_route', return_value=Mock(provider_id='qwen')),
              patch('app.api.pipeline.resolve_available_voice', new=AsyncMock(return_value='voice'))):
            history.get_task.return_value = {'task_id': 'one'}
            await start_pipeline(request)
            self.assertEqual(start.call_args.kwargs['output_options'], request.output_options.model_dump())
            # The service persists these only after accepting the launch.
            history.update_task.assert_not_called()

    async def test_compression_resume_keeps_output_options(self):
        choices = VideoOutputOptions(policy='custom', codec='h264', bitrate_mbps=8).model_dump()
        request = dict(target_lang='Chinese', voice='voice', speed=1, burn_subtitles=True,
                       show_source=False, show_target=True, subtitle_style={}, subtitle_format='srt',
                       output_options=choices, end_note_enabled=False)
        with (patch('app.api.pipeline.history_manager') as history,
              patch('app.api.pipeline.emit_event'),
              patch('app.api.pipeline.pipeline_service.start') as start):
            history.get_task.return_value = dict(pipeline_status='awaiting_compression', compression_request=request)
            await decide_compression('one', CompressionDecision(approve=True))
            self.assertEqual(start.call_args.kwargs['output_options'], choices)
            self.assertFalse(start.call_args.kwargs['end_note_enabled'])

    async def test_render_stage_gets_choices_and_reuses_existing_speech(self):
        from app.services.pipeline_service import PipelineService
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing'; path.write_bytes(b'fixture')
            task = dict(task_id='one', transcription_path=str(path), translations={'Chinese': str(path)},
                        dubbing_status='completed', dubbing_target_lang='Chinese', dubbing_voice='voice',
                        dubbing_speed=1, dubbing_audio_path=str(path), dubbing_raw_video_path=str(path))
            history = Mock()
            history.get_task.return_value = task
            history.update_task.side_effect = lambda _, values: task.update(values)
            burn = AsyncMock(side_effect=lambda *_args, **_kwargs: task.update(dubbing_video_path=str(path)))
            dub = AsyncMock()
            options = {'policy': 'resolution'}
            with patch('app.services.pipeline_service.history_manager', history), patch('app.services.pipeline_service.emit_event'), patch.object(PipelineService, '_review_translation', return_value=False):
                await PipelineService(burn_stage=burn, dub_stage=dub).run(
                    'one', 'Chinese', 'voice', end_note_enabled=True, output_options=options)
            burn.assert_awaited_once()
            self.assertEqual(burn.await_args.kwargs['output_options'], options)
            dub.assert_not_awaited()
            self.assertEqual(task['pipeline_status'], 'completed')

    async def test_missing_task_returns_404(self):
        with patch('app.api.tasks.history_manager') as history:
            history.get_task.return_value = None
            with self.assertRaises(HTTPException) as error:
                await preview_video_plan('missing', VideoPlanRequest(output_options={}))
        self.assertEqual(error.exception.status_code, 404)

    async def test_source_path_is_confined_to_uploads(self):
        with patch('app.api.tasks.history_manager') as history:
            history.get_task.return_value = {'upload_path': __file__}
            with self.assertRaises(HTTPException) as error:
                await preview_video_plan('one', VideoPlanRequest(output_options={}))
        self.assertEqual(error.exception.status_code, 404)

    async def test_returns_real_probe_and_clearly_marked_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'input.mkv'; source.touch()
            with (patch('app.api.tasks.settings.UPLOAD_DIR', root),
                  patch('app.api.tasks.history_manager') as history,
                  patch('app.services.video_burn_service.video_burn_service._probe_video_profile', return_value=SOURCE) as probe):
                history.get_task.return_value = {'upload_path': str(source)}
                response = await preview_video_plan('one', VideoPlanRequest(output_options={}, end_note_enabled=False))
                probe.assert_called_once_with(source.resolve())
                self.assertEqual(response['source'], SOURCE)
                self.assertEqual(response['plan']['encoder'], 'copy')
                self.assertEqual(response['source']['bitrate_source'], 'container_estimate')
