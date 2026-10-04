import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings
from app.core.history import history_manager
from app.core.events import emit_event
from app.core.schemas import TranscriptionResult
from app.services.formatter_service import subtitle_formatter

logger = logging.getLogger(__name__)
END_NOTE_URL = "https://github.com/jacky0jia/video_translator"


class VideoBurnService:
    def __init__(self):
        self.ffmpeg_path = settings.ffmpeg_path
        self.gpu_encoder = self._detect_gpu_encoder()

    def _detect_gpu_encoder(self) -> Optional[str]:
        """Detect available GPU encoder by checking ffmpeg -encoders."""
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-encoders"],
                capture_output=True, text=True, check=True
            )
            encoders = result.stdout
            if "h264_nvenc" in encoders:
                logger.info("GPU encoder detected: h264_nvenc (NVIDIA)")
                return "h264_nvenc"
            if "h264_qsv" in encoders:
                logger.info("GPU encoder detected: h264_qsv (Intel)")
                return "h264_qsv"
            if "h264_amf" in encoders:
                logger.info("GPU encoder detected: h264_amf (AMD)")
                return "h264_amf"
        except Exception as e:
            logger.warning(f"Failed to detect GPU encoder: {e}")
        logger.info("No GPU encoder detected, falling back to libx264")
        return None

    def _load_transcription(self, path: str) -> Optional[TranscriptionResult]:
        try:
            p = Path(path)
            if not p.exists():
                return None
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return TranscriptionResult(**data)
        except Exception as e:
            logger.error(f"Failed to load transcription from {path}: {e}")
            return None

    def _get_duration(self, result: Optional[TranscriptionResult]) -> float:
        if result and result.segments:
            return result.segments[-1].end
        return 0.0

    def _probe_video_profile(self, video_path: Path) -> Dict[str, Any]:
        """Record source parameters before subtitle burn requires a re-encode."""
        try:
            ffprobe_path = Path(self.ffmpeg_path).parent / "ffprobe.exe"
            if not ffprobe_path.exists():
                ffprobe_path = Path(self.ffmpeg_path).parent / "ffprobe"
            cmd = [
                str(ffprobe_path),
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height,bit_rate,avg_frame_rate,pix_fmt,color_space,color_transfer,color_primaries:format=duration",
                "-of", "json",
                str(video_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            stream = data["streams"][0]
            bitrate = stream.get("bit_rate")
            duration = float((data.get("format") or {}).get("duration") or 0)
            profile = {
                "probed": True, "codec": str(stream.get("codec_name") or "unknown"),
                "width": int(stream["width"]), "height": int(stream["height"]),
                "duration": duration if duration > 0 else None,
                "bitrate": int(bitrate) if str(bitrate or "").isdigit() else None,
                "frame_rate": str(stream.get("avg_frame_rate") or "unknown"),
                "pixel_format": str(stream.get("pix_fmt") or "unknown"),
                "color_space": str(stream.get("color_space") or "unknown"),
                "color_transfer": str(stream.get("color_transfer") or "unknown"),
                "color_primaries": str(stream.get("color_primaries") or "unknown"),
            }
            logger.info("Source video profile before subtitle burn: %s", profile)
            return profile
        except Exception as e:
            logger.warning("Failed to probe source video profile: %s", e)
            return {"probed": False, "codec": "unknown", "width": 1920, "height": 1080,
                    "duration": None, "bitrate": None, "frame_rate": "unknown", "pixel_format": "unknown",
                    "color_space": "unknown", "color_transfer": "unknown", "color_primaries": "unknown"}

    def _get_video_bitrate(self, video_path: Path) -> Optional[int]:
        """Return the source video bitrate so burn-in does not inflate files."""
        try:
            ffprobe_path = Path(self.ffmpeg_path).parent / "ffprobe.exe"
            if not ffprobe_path.exists():
                ffprobe_path = Path(self.ffmpeg_path).parent / "ffprobe"
            result = subprocess.run(
                [
                    str(ffprobe_path), "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=bit_rate", "-of", "json",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            value = (json.loads(result.stdout).get("streams") or [{}])[0].get("bit_rate")
            bitrate = int(value or 0)
            return bitrate if bitrate > 0 else None
        except Exception as exc:
            logger.warning("Failed to probe source video bitrate: %s", exc)
            return None

    @staticmethod
    def _video_encode_args(encoder: Optional[str], source_bitrate: Optional[int] = None) -> list[str]:
        """Use quality-based H.264; AV1/HEVC bitrate is not a safe H.264 target."""
        if encoder == "h264_nvenc":
            return [
                "-c:v", encoder, "-preset", "p7", "-tune", "hq", "-rc", "vbr",
                "-multipass", "fullres", "-cq", "18", "-b:v", "0",
                "-spatial_aq", "1", "-temporal_aq", "1", "-rc-lookahead", "32",
            ]
        if encoder == "h264_qsv":
            return ["-c:v", encoder, "-global_quality", "18", "-preset", "slow"]
        if encoder == "h264_amf":
            return [
                "-c:v", encoder, "-quality", "quality", "-rc", "cqp",
                "-qp_p", "18", "-qp_i", "18",
            ]
        return ["-c:v", "libx264", "-preset", "slow", "-crf", "18"]

    @staticmethod
    def _video_output_args(profile: Dict[str, Any]) -> list[str]:
        args = ["-fps_mode", "passthrough", "-pix_fmt", "yuv420p", "-map_metadata", "0"]
        for source_key, option in (("color_space", "-colorspace"),
                                   ("color_transfer", "-color_trc"),
                                   ("color_primaries", "-color_primaries")):
            value = profile.get(source_key)
            if value and value != "unknown":
                args.extend((option, str(value)))
        return args

    def _generate_ass(
        self,
        task: Dict[str, Any],
        show_source: bool,
        show_target: bool,
        style: Dict[str, Any],
        temp_dir: Path,
        target_lang: Optional[str] = None,
        playres_x: int = 1920,
        playres_y: int = 1080,
        end_note_enabled: bool = False,
        video_duration: float = 0,
        dubbed: bool = False,
    ) -> Tuple[Path, float]:
        logger.info(f"Generating ASS for task {task['task_id']}: show_source={show_source}, show_target={show_target}, target_lang={target_lang}")
        original_result = None
        result = None

        if show_source and task.get("transcription_path"):
            logger.info(f"Loading source subtitle from {task['transcription_path']}")
            original_result = self._load_transcription(task["transcription_path"])
            if original_result:
                logger.info(f"Source subtitle loaded: {len(original_result.segments)} segments")

        if show_target:
            trans_path = None
            if target_lang and task.get("translations"):
                trans_path = task["translations"].get(target_lang)
            if not trans_path:
                trans_path = task.get("translation_path")
            if not trans_path and task.get("translations"):
                trans_path = list(task["translations"].values())[0]
            if trans_path:
                logger.info(f"Loading target subtitle from {trans_path}")
                result = self._load_transcription(trans_path)
                if result:
                    logger.info(f"Target subtitle loaded: {len(result.segments)} segments")

        if not result and not original_result and not end_note_enabled:
            raise ValueError("No subtitle data available for burn-in")

        if show_source and not original_result:
            raise ValueError("Source subtitle data not found")
        if show_target and not result:
            raise ValueError("Target subtitle data not found")

        ass_content = subtitle_formatter.to_ass(
            result=result or original_result or TranscriptionResult(video_source="", language="", segments=[]),
            original_result=original_result if show_source else None,
            source_color=style.get("sourceColor"),
            target_color=style.get("targetColor"),
            font_size=style.get("fontSize"),
            offset_y=style.get("offsetY"),
            show_source=show_source,
            show_target=show_target,
            playres_x=playres_x,
            playres_y=playres_y,
            bold=style.get("bold"),
            font_family=style.get("fontFamily"),
        )

        if end_note_enabled:
            if video_duration <= 0:
                raise ValueError("Video duration is required for the end note")
            ass_content = self._append_end_note(ass_content, video_duration, playres_x, playres_y, dubbed)

        ass_path = temp_dir / f"burn_{task['task_id']}.ass"
        with open(ass_path, "w", encoding="utf-8-sig") as f:
            f.write(ass_content)

        # Log first few lines of ASS for debugging subtitle width
        logger.info(f"ASS file generated at {ass_path}, first 15 lines:\n" + "\n".join(ass_content.splitlines()[:15]))

        duration = max(video_duration, self._get_duration(original_result), self._get_duration(result))
        return ass_path, duration

    @staticmethod
    def _append_end_note(content: str, duration: float, width: int, height: int, dubbed: bool) -> str:
        """Overlay a visual-only credit during the existing final three seconds."""
        font_size = max(12, min(30, round(width * 0.0125)))
        margin = max(12, round(width * 0.03))
        top = max(12, round(height * 0.04))
        start = subtitle_formatter._format_timestamp(max(0, duration - 3), "ass")
        end = subtitle_formatter._format_timestamp(duration, "ass")
        description = "AI-assisted translation and dubbing with Video Translator" if dubbed else "AI-assisted translation with Video Translator"
        style = (
            f"Style: EndNote,Arial,{font_size},&H00FFFFFF,&H00000000,&H00000000,&H60000000,"
            f"0,0,0,0,100,100,0,0,3,6,0,9,{margin},{margin},{top},1"
        )
        event = (
            f"Dialogue: 1,{start},{end},EndNote,,0,0,0,,"
            f"{{\\an9\\pos({width - margin},{top})}}{description}\\N{END_NOTE_URL}"
        )
        return content.replace("\n[Events]", f"\n{style}\n\n[Events]", 1) + "\n" + event

    def _parse_time(self, time_str: str) -> float:
        parts = time_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])

    def _to_download_url(self, file_path: Path) -> str:
        try:
            rel = file_path.relative_to(settings.OUTPUT_DIR)
            return f"/static/output/{rel.as_posix()}"
        except ValueError:
            return str(file_path)

    def burn(
        self,
        task_id: str,
        show_source: bool,
        show_target: bool,
        style: Dict[str, Any],
        target_lang: Optional[str] = None,
        video_path_override: Optional[str] = None,
        output_field: str = "burn_path",
        end_note_enabled: bool = False,
    ) -> str:
        if output_field not in {"burn_path", "dubbing_video_path"}:
            raise ValueError("Unsupported burn output field")

        task = history_manager.get_task(task_id)
        if not task:
            raise ValueError("Task not found")

        video_path = video_path_override or task.get("upload_path") or task.get("source_path")
        if not video_path:
            raise ValueError("Source video path not found")

        video_path_obj = Path(video_path)
        if not video_path_obj.exists():
            raise ValueError("Source video file not found")

        temp_dir = Path(settings.TEMP_DIR)
        temp_dir.mkdir(parents=True, exist_ok=True)

        ass_path: Optional[Path] = None
        lang_display = target_lang or task.get("target_lang") or ""
        try:
            source_profile = self._probe_video_profile(video_path_obj)
            history_manager.update_task(task_id, {"source_video_profile": source_profile})
            video_width, video_height = source_profile["width"], source_profile["height"]
            ass_path, duration = self._generate_ass(
                task, show_source, show_target, style, temp_dir, target_lang, video_width, video_height,
                end_note_enabled, source_profile.get("duration") or 0, output_field == "dubbing_video_path",
            )

            stem = Path(task["filename"]).stem
            ts = time.strftime("%Y%m%d_%H%M%S")
            suffix = ("dubbed_subtitled" if show_source or show_target else "dubbed_endnote") if output_field == "dubbing_video_path" else "burned"
            output_name = f"{stem}_{suffix}_{ts}.mp4"
            output_path = settings.OUTPUT_DIR / output_name
            settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            encode_args = self._video_encode_args(self.gpu_encoder, source_profile["bitrate"])
            output_args = self._video_output_args(source_profile)
            logger.info(
                "Burn quality: source=%s encoder=%s args=%s",
                source_profile,
                self.gpu_encoder or "libx264",
                encode_args,
            )
            cmd = [
                self.ffmpeg_path, "-y", "-i", str(video_path_obj),
                "-vf", f"ass={ass_path.name}",
                *encode_args,
                *output_args,
                "-c:a", "copy",
                str(output_path),
            ]

            logger.info(f"Starting burn for task {task_id}: {' '.join(cmd)}")

            history_manager.update_task(task_id, {"burn_status": "processing"})
            emit_event(task_id, {"status": "burning", "message": "started", "target_lang": lang_display, "progress": 0})

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(temp_dir),
            )

            stderr_lines = []

            def read_stderr():
                last_progress = 0
                for line in iter(process.stderr.readline, b""):
                    stderr_lines.append(line)
                    line_str = line.decode("utf-8", errors="replace")
                    match = re.search(r"time=(\d+:\d+:\d+\.\d+)", line_str)
                    if match and duration > 0:
                        current_time = self._parse_time(match.group(1))
                        progress = min(int((current_time / duration) * 100), 99)
                        if progress > last_progress:
                            last_progress = progress
                            emit_event(task_id, {"status": "burning", "message": "processing", "target_lang": lang_display, "progress": progress})

            stderr_thread = threading.Thread(target=read_stderr, daemon=True)
            stderr_thread.start()
            process.wait()
            stderr_thread.join(timeout=2)

            if process.returncode != 0:
                err = b"".join(stderr_lines).decode("utf-8", errors="replace")[-500:] if stderr_lines else "Unknown FFmpeg error"
                # If GPU encoder failed, fallback to libx264 once
                if self.gpu_encoder:
                    logger.warning(f"GPU encoder {self.gpu_encoder} failed: {err}. Falling back to libx264.")
                    self.gpu_encoder = None
                    cmd = [
                        self.ffmpeg_path, "-y", "-i", str(video_path_obj),
                        "-vf", f"ass={ass_path.name}",
                        *self._video_encode_args(None, source_profile["bitrate"]),
                        *output_args,
                        "-c:a", "copy",
                        str(output_path),
                    ]
                    logger.info(f"Retrying burn with software encoder for task {task_id}: {' '.join(cmd)}")
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=str(temp_dir),
                    )
                    stderr_lines = []
                    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
                    stderr_thread.start()
                    process.wait()
                    stderr_thread.join(timeout=2)
                    if process.returncode == 0:
                        output_profile = self._probe_video_profile(output_path)
                        if source_profile["probed"] and output_profile["probed"] and (output_profile["width"], output_profile["height"]) != (video_width, video_height):
                            raise RuntimeError("Rendered video resolution differs from the source")
                        download_url = self._to_download_url(output_path)
                        history_manager.update_task(task_id, {"burn_status": "completed", output_field: download_url,
                                                              "output_video_profile": output_profile})
                        emit_event(task_id, {"status": "burning_completed", "message": "completed", "target_lang": lang_display, "progress": 100})
                        logger.info(f"Burn completed (fallback) for task {task_id}: {output_path}")
                        return download_url
                logger.error(f"FFmpeg burn failed: {err}")
                history_manager.update_task(task_id, {"burn_status": "failed", "burn_error": err})
                emit_event(task_id, {"status": "burning_failed", "message": "failed", "target_lang": lang_display, "progress": 0})
                raise RuntimeError(f"FFmpeg burn failed: {err}")

            output_profile = self._probe_video_profile(output_path)
            if source_profile["probed"] and output_profile["probed"] and (output_profile["width"], output_profile["height"]) != (video_width, video_height):
                raise RuntimeError("Rendered video resolution differs from the source")
            download_url = self._to_download_url(output_path)
            history_manager.update_task(task_id, {"burn_status": "completed", output_field: download_url,
                                                  "output_video_profile": output_profile})
            emit_event(task_id, {"status": "burning_completed", "message": "completed", "target_lang": lang_display, "progress": 100})
            logger.info(f"Burn completed for task {task_id}: {output_path}")
            return download_url

        except Exception as e:
            logger.exception(f"Burn failed for task {task_id}")
            history_manager.update_task(task_id, {"burn_status": "failed", "burn_error": str(e)})
            emit_event(task_id, {"status": "burning_failed", "message": "failed", "target_lang": lang_display, "progress": 0})
            raise
        finally:
            # Keep ASS file for debugging; clean up manually if needed
            pass


video_burn_service = VideoBurnService()
