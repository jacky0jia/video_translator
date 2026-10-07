"""Source-codec-preserving, size-aware plans for subtitle/end-note rendering."""
import os
import re
import subprocess


ENCODERS = {
    'av1': ('av1_nvenc', 'av1_qsv', 'av1_amf', 'libsvtav1', 'libaom-av1'),
    'hevc': ('hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'libx265'),
    'h264': ('h264_nvenc', 'h264_qsv', 'h264_amf', 'libx264'),
    'vp9': ('vp9_qsv', 'libvpx-vp9'),
    'mpeg4': ('mpeg4',),
}
SOFTWARE = {'libsvtav1', 'libaom-av1', 'libx265', 'libx264', 'libvpx-vp9', 'mpeg4'}


def encode_args(encoder, bitrate=None):
    args = ['-c:v', encoder]
    if encoder.endswith('_nvenc'):
        args += ['-preset', 'p5', '-tune', 'hq', '-rc', 'vbr', '-multipass', 'fullres']
    elif encoder.endswith('_qsv'):
        args += ['-preset', 'medium']
    elif encoder.endswith('_amf'):
        args += ['-quality', 'quality', '-rc', 'vbr_peak']
    elif encoder == 'libsvtav1':
        args += ['-preset', '8']
    elif encoder == 'libaom-av1':
        args += ['-cpu-used', '6', '-row-mt', '1', '-threads', str(min(os.cpu_count() or 4, 8))]
    elif encoder in {'libx264', 'libx265'}:
        args += ['-preset', 'medium']
    elif encoder == 'libvpx-vp9':
        args += ['-deadline', 'good', '-cpu-used', '4']
    if bitrate and bitrate > 0:
        # Same codec: source average is a meaningful size target. VBR may vary;
        # this is not a lossless promise or a strict final-byte guarantee.
        args += ['-b:v', str(int(bitrate)), '-maxrate', str(int(bitrate * 1.25)),
                 '-bufsize', str(int(bitrate * 2))]
    elif encoder.endswith('_nvenc'):
        args += ['-cq', '26', '-b:v', '0']
    elif encoder.endswith('_qsv'):
        args += ['-global_quality', '26']
    elif encoder.endswith('_amf'):
        # No useful average bitrate: use codec-specific quality control instead.
        args[args.index('vbr_peak')] = 'cqp'
        if encoder.startswith('av1_'):
            args += ['-qp_i', '100', '-qp_p', '100']
        else:
            args += ['-qp_i', '23', '-qp_p', '23']
    elif encoder == 'mpeg4':
        args += ['-q:v', '4']
    else:
        args += ['-crf', '30' if encoder in {'libaom-av1', 'libsvtav1', 'libvpx-vp9'} else '23']
        if encoder in {'libaom-av1', 'libvpx-vp9'}:
            args += ['-b:v', '0']
    return args


def select_encoder(ffmpeg, profile, *, software_only=False):
    codec = profile.get('codec')
    if not profile.get('probed') or codec not in ENCODERS:
        raise RuntimeError(f'Cannot preserve source video codec ({codec or "unknown"}). '
                           'Disable burned subtitles and the end note to keep the original video stream, '
                           'or convert the source to a supported codec (AV1, HEVC, H.264, VP9, MPEG-4).')
    result = subprocess.run([ffmpeg, '-hide_banner', '-encoders'], capture_output=True,
                            text=True, check=True, timeout=15)
    available = set(re.findall(r'^\s*V\S*\s+(\S+)', result.stdout, re.M))
    for encoder in ENCODERS[codec]:
        if encoder not in available or (software_only and encoder not in SOFTWARE):
            continue
        if encoder not in SOFTWARE:
            # Compiled-in support is not proof that this machine can encode it.
            # Use the actual frame dimensions to catch hardware resolution limits.
            try:
                probe = subprocess.run([
                    ffmpeg, '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=s={profile["width"]}x{profile["height"]}:r=1',
                    '-frames:v', '1', *encode_args(encoder, profile.get('bitrate')),
                    '-pix_fmt', 'yuv420p', '-f', 'null', '-',
                ], capture_output=True, timeout=20)
                if probe.returncode:
                    continue
            except (OSError, subprocess.TimeoutExpired):
                continue
        return {'codec': codec, 'encoder': encoder, 'software': encoder in SOFTWARE,
                'bitrate': profile.get('bitrate'),
                'bitrate_source': profile.get('bitrate_source', 'unknown')}
    raise RuntimeError(f'No usable {codec.upper()} encoder is available. No automatic H.264 conversion '
                       'was performed. Install an FFmpeg build with a matching software encoder, '
                       'or disable burned subtitles and the end note to retain the original video stream.')
