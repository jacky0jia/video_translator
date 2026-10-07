"""Validated output choices and bounded, capability-tested video plans."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.video_encoding import select_encoder


class VideoOutputOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy: Literal['balanced', 'resolution', 'codec', 'custom'] = 'balanced'
    codec: Literal['hevc', 'h264', 'av1'] = 'hevc'
    resolution: Literal['source', '1440', '1080'] = '1440'
    bitrate_mbps: float = Field(default=10, ge=0.1, le=200, allow_inf_nan=False)


def fit_dimensions(width: int, height: int, resolution: str) -> tuple[int, int]:
    if resolution == 'source':
        return width, height
    # Bound the short and long sides, including portrait videos; never upscale.
    short = int(resolution)
    long = round(short * 16 / 9)
    scale = min(1, short / min(width, height), long / max(width, height))
    if scale == 1:
        return width, height
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def make_output_plan(ffmpeg: str, source: dict, options: VideoOutputOptions | dict | None = None,
                     *, overlays: bool = True, excluded_encoders: tuple[str, ...] = ()) -> dict:
    choices = options if isinstance(options, VideoOutputOptions) else VideoOutputOptions(**(options or {}))
    if not source.get('probed'):
        raise ValueError('Source video parameters are unavailable. Try importing the video again.')
    width, height = source['width'], source['height']
    codec = source['codec']
    if not overlays:
        plan = {'codec': codec, 'encoder': 'copy', 'software': False, 'bitrate': source.get('bitrate'),
                'width': width, 'height': height, 'reason': 'stream_copy'}
    else:
        def choose(target_codec, resolution='source', *, hardware_only=False, reason='source_preserved'):
            w, h = fit_dimensions(width, height, resolution)
            rate = source.get('bitrate')
            estimated = not rate
            if not rate:
                # Missing source rate: a bounded pixel budget, not unbounded CRF.
                try:
                    numerator, denominator = source.get('frame_rate', '30/1').split('/')
                    fps = min(120, max(1, float(numerator) / float(denominator)))
                except (ValueError, ZeroDivisionError):
                    fps = 30
                rate = max(500_000, min(40_000_000, width * height * fps * 0.07))
            factor = (w * h / (width * height)) ** 0.65
            if target_codec != codec:
                factor *= 1.2 if target_codec == 'hevc' else 1.6
            if choices.policy == 'balanced':
                factor = min(1, factor)
            rate = int(rate * factor)
            if choices.policy == 'custom':
                rate = int(choices.bitrate_mbps * 1_000_000)
            target = {**source, 'codec': target_codec, 'width': w, 'height': h, 'bitrate': rate}
            selected = select_encoder(ffmpeg, target, hardware_only=hardware_only,
                                      excluded_encoders=excluded_encoders)
            return {**selected, 'width': w, 'height': h, 'reason': reason,
                    'bitrate_estimated': estimated}

        if choices.policy == 'custom':
            plan = choose(choices.codec, choices.resolution, reason='custom')
        elif codec != 'av1' or choices.policy == 'codec':
            plan = choose(codec)
        else:
            try:
                plan = choose('av1', hardware_only=True)
            except RuntimeError:
                resolution = '1440' if choices.policy == 'balanced' else 'source'
                reason = 'av1_balanced_fallback' if choices.policy == 'balanced' else 'av1_resolution_fallback'
                plan = None
                for target, hardware in [('hevc', True), ('h264', True), ('hevc', False), ('h264', False)]:
                    # Balanced CPU fallback favors the faster H.264 encoder.
                    if choices.policy == 'balanced' and target == 'hevc' and not hardware:
                        continue
                    try:
                        plan = choose(target, resolution, hardware_only=hardware, reason=reason)
                        break
                    except RuntimeError:
                        continue
                if plan is None:
                    raise RuntimeError('No usable balanced video encoder. Choose Keep source codec to use AV1 software encoding.')
    duration = source.get('duration') or 0
    rate = plan.get('bitrate') or 0
    plan.update(policy=choices.policy, frame_rate=source.get('frame_rate'),
                bitrate_source=source.get('bitrate_source', 'unknown'),
                estimated_bytes=int((rate + 192_000) * duration / 8) if rate and duration else None)
    return plan
