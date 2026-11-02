"""Real 4K AV1 -> balanced 1440p smoke, without AI models or user media.

Run with the application's Python; pass an explicit FFmpeg binary as argv[1].
All generated media lives in a temporary directory and is removed afterwards.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.video_burn_service import VideoBurnService
from app.services.video_encoding import select_encoder
from app.services.video_output import make_output_plan


def main():
    ffmpeg = str(Path(sys.argv[1]).resolve())
    service = VideoBurnService()
    service.ffmpeg_path = ffmpeg
    with tempfile.TemporaryDirectory(prefix='video-output-smoke-') as directory:
        root = Path(directory)
        source = root / 'source.mkv'
        subprocess.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=3840x2160:rate=60', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=48000', '-t', '0.3',
                        '-c:v', 'libaom-av1', '-cpu-used', '8', '-row-mt', '1',
                        '-threads', '4', '-b:v', '12000000', '-c:a', 'aac', str(source)],
                       check=True, capture_output=True, timeout=120)
        original = service._probe_video_profile(source)
        assert original['codec'] == 'av1' and original['height'] == 2160
        # Exercise the explicitly simulated no-AV1/HEVC-hardware route, while
        # performing real software selection, encode, ASS burn and ffprobe.
        def cpu_only(ffmpeg, profile, *, hardware_only=False, **kwargs):
            if hardware_only:
                raise RuntimeError('Simulated unavailable hardware')
            return select_encoder(ffmpeg, profile, software_only=True)
        history = Mock()
        history.get_task.return_value = dict(task_id='output-smoke', filename=source.name, upload_path=str(source))
        history.update_task.side_effect = lambda _, values: history.get_task.return_value.update(values)
        with (patch('app.services.video_output.select_encoder', side_effect=cpu_only),
              patch('app.services.video_burn_service.history_manager', history),
              patch('app.services.video_burn_service.emit_event'),
              patch('app.services.video_burn_service.settings.TEMP_DIR', root / 'temp'),
              patch('app.services.video_burn_service.settings.OUTPUT_DIR', root / 'output')):
            plan = make_output_plan(ffmpeg, original)
            url = service.burn('output-smoke', False, False, {}, output_field='dubbing_video_path',
                               end_note_enabled=True, output_options={'policy': 'balanced'})
            output = root / 'output' / url.rsplit('/', 1)[-1]
            actual = service._probe_video_profile(output)
            assert (actual['codec'], actual['width'], actual['height']) == ('h264', 2560, 1440), actual
            assert actual['frame_rate'] == original['frame_rate'], actual
            ass = (root / 'temp' / 'burn_output-smoke.ass').read_text(encoding='utf-8-sig')
            assert 'PlayResX: 2560' in ass and 'PlayResY: 1440' in ass
            audio_probe = subprocess.run([str(Path(ffmpeg).with_name('ffprobe.exe')), '-v', 'error',
                                          '-select_streams', 'a:0', '-show_entries', 'stream=codec_name',
                                          '-of', 'json', str(output)], check=True, capture_output=True, text=True)
            assert json.loads(audio_probe.stdout)['streams'][0]['codec_name'] == 'aac'
            print(json.dumps({'source': original, 'planned': plan, 'actual': actual,
                              'ass_position_scale': '2560x1440', 'audio': 'aac', 'passed': True}))


if __name__ == '__main__':
    main()
