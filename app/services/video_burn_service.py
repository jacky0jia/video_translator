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

    def _get_video_resolution(self, video_path: Path) -> Tuple[int, int]:
        """Get video width and height using ffprobe."""
        try:
            ffprobe_path = Path(self.ffmpeg_path).parent / "ffprobe.exe"
            if not ffprobe_path.exists():
                ffprobe_path = Path(self.ffmpeg_path).parent / "ffprobe"
            cmd = [
                str(ffprobe_path),
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                str(video_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            stream = data["streams"][0]
            width = int(stream["width"])
            height = int(stream["height"])
            logger.info(f"Video resolution detected: {width}x{height} for {video_path}")
            return width, height
        except Exception as e:
            logger.warning(f"Failed to get video resolution, using defaults: {e}")
            return 1920, 1080

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
    def _video_encode_args(encoder: Optional[str], source_bitrate: Optional[int]) -> list[str]:
        """Stay near the source size while leaving headroom for scene transitions."""
        if source_bitrate:
            # Burning subtitles always requires a full re-encode. A max rate close
            # to the source average starves sudden scene changes and produces a
            # short burst of macroblocking. Reserve a little of the average budget
            # for those peaks, then give the encoder a wider VBV window.
            target = str(int(source_bitrate * 0.92))
            maxrate = str(source_bitrate * 3)
            bufsize = str(source_bitrate * 4)
            if encoder == "h264_nvenc":
                return [
                    "-c:v", encoder, "-preset", "p7", "-tune", "hq", "-rc", "vbr",
                    "-multipass", "fullres", "-profile:v", "high", "-b:v", target,
                    "-maxrate", maxrate, "-bufsize", bufsize,
                    "-spatial_aq", "1", "-temporal_aq", "1", "-rc-lookahead", "32",
                    "-bf", "3", "-b_ref_mode", "middle",
                ]
            if encoder == "h264_qsv":
                return [
                    "-c:v", encoder, "-preset", "slow", "-b:v", target,
                    "-maxrate", maxrate, "-bufsize", bufsize,
                ]
            if encoder == "h264_amf":
                return [
                    "-c:v", encoder, "-quality", "quality", "-rc", "vbr_peak",
                    "-b:v", target, "-maxrate", maxrate, "-bufsize", bufsize,
                ]
            return [
                "-c:v", "libx264", "-preset", "slow", "-b:v", target,
                "-maxrate", maxrate, "-bufsize", bufsize,
            ]
        if encoder == "h264_nvenc":
            return [
                "-c:v", encoder, "-preset", "p5", "-rc", "vbr",
                "-cq", "20", "-b:v", "0",
            ]
        if encoder == "h264_qsv":
            return ["-c:v", encoder, "-global_quality", "20", "-preset", "medium"]
        if encoder == "h264_amf":
            return [
                "-c:v", encoder, "-quality", "quality", "-rc", "cqp",
                "-qp_p", "20", "-qp_i", "20",
            ]
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]

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

        if not result and not original_result:
            raise ValueError("No subtitle data available for burn-in")

        if show_source and not original_result:
            raise ValueError("Source subtitle data not found")
        if show_target and not result:
            raise ValueError("Target subtitle data not found")

        ass_content = subtitle_formatter.to_ass(
            result=result or original_result,
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

        ass_path = temp_dir / f"burn_{task['task_id']}.ass"
        with open(ass_path, "w", encoding="utf-8-sig") as f:
            f.write(ass_content)

        # Log first few lines of ASS for debugging subtitle width
        logger.info(f"ASS file generated at {ass_path}, first 15 lines:\n" + "\n".join(ass_content.splitlines()[:15]))

        duration = max(self._get_duration(original_result), self._get_duration(result))
        return ass_path, duration

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
            video_width, video_height = self._get_video_resolution(video_path_obj)
            ass_path, duration = self._generate_ass(task, show_source, show_target, style, temp_dir, target_lang, video_width, video_height)

            stem = Path(task["filename"]).stem
            ts = time.strftime("%Y%m%d_%H%M%S")
            suffix = "dubbed_subtitled" if output_field == "dubbing_video_path" else "burned"
            output_name = f"{stem}_{suffix}_{ts}.mp4"
            output_path = settings.OUTPUT_DIR / output_name
            settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            source_bitrate = self._get_video_bitrate(video_path_obj)
            encode_args = self._video_encode_args(self.gpu_encoder, source_bitrate)
            logger.info(
                "Burn rate control: source_video_bitrate=%s encoder=%s args=%s",
                source_bitrate,
                self.gpu_encoder or "libx264",
                encode_args,
            )
            cmd = [
                self.ffmpeg_path, "-y", "-i", str(video_path_obj),
                "-vf", f"ass={ass_path.name}",
                *encode_args,
                "-pix_fmt", "yuv420p",
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
                        *self._video_encode_args(None, source_bitrate),
                        "-pix_fmt", "yuv420p",
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
                        download_url = self._to_download_url(output_path)
                        history_manager.update_task(task_id, {"burn_status": "completed", output_field: download_url})
                        emit_event(task_id, {"status": "burning_completed", "message": "completed", "target_lang": lang_display, "progress": 100})
                        logger.info(f"Burn completed (fallback) for task {task_id}: {output_path}")
                        return download_url
                logger.error(f"FFmpeg burn failed: {err}")
                history_manager.update_task(task_id, {"burn_status": "failed", "burn_error": err})
                emit_event(task_id, {"status": "burning_failed", "message": "failed", "target_lang": lang_display, "progress": 0})
                raise RuntimeError(f"FFmpeg burn failed: {err}")

            download_url = self._to_download_url(output_path)
            history_manager.update_task(task_id, {"burn_status": "completed", output_field: download_url})
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
