"""Opt-in real GPU pipeline acceptance with isolated history and artifacts."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys


def validate_alignment(source, translated):
    if not source or len(source) != len(translated):
        raise RuntimeError("Source and target segment counts differ or are empty")
    for original, target in zip(source, translated):
        if (original["start"], original["end"]) != (target["start"], target["end"]):
            raise RuntimeError("Translation changed source timing")
        if not target["text"].strip() or target["text"] in {"Translation error", "[Translation failed]"}:
            raise RuntimeError("Translation is empty or failed")


def validate_review_rows(rows, segment_count):
    if any(type(row) is not int or not 1 <= row <= segment_count for row in rows):
        raise RuntimeError("Alignment review contains an invalid row")


def main():
    public = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=public)
    parser.add_argument("--assets", type=Path, default=public)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to((public / ".codex-test-runs").resolve())
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.repo.resolve()))
    from app.core.config import settings
    # Initialize the singleton history in the disposable root before importing
    # services; restore BASE_DIR only for read-only model asset resolution.
    settings.BASE_DIR = output / "app"
    settings.BASE_DIR.mkdir()
    settings.TEMP_DIR = output / "temp"
    settings.OUTPUT_DIR = output / "output"
    settings.UPLOAD_DIR = output / "uploads"
    settings.CONFIG_PATH = output / "config.yaml"
    for directory in (settings.TEMP_DIR, settings.OUTPUT_DIR, settings.UPLOAD_DIR):
        directory.mkdir()
    from app.core.history import history_manager
    assert history_manager.history_file.resolve() == output / "app" / "history.json"
    settings.BASE_DIR = args.assets.resolve() / "app"
    settings.FFMPEG_PATH = str(args.assets.resolve() / "ffmpeg" / "ffmpeg.exe")
    settings.ASR_API_URL = None
    settings.ASR_MODEL_PATH = str(args.assets.resolve() / "models" / "faster-whisper-small")
    settings.DEVICE_PREFERENCE = "gpu"
    settings.COMPUTE_TYPE = "float16"
    settings.TTS_MODE = "qwen"
    settings.QWEN_AUTO_CPU_FALLBACK = False
    from app.api.pipeline import PipelineRequest, start_pipeline
    from app.services.pipeline_service import pipeline_service
    from app.services.tts.qwen import get_qwen_runtime_status
    from fastapi import HTTPException
    logging.basicConfig(level=logging.INFO)
    source = settings.UPLOAD_DIR / "acceptance.mp4"
    shutil.copy2(args.source, source)
    task_id = history_manager.create_task(source.name, "file", str(source), "Chinese")
    history_manager.update_task(task_id, {"language": "en"})

    async def run():
        payload = PipelineRequest(task_id=task_id, target_lang="Chinese", voice="zh_female_1",
                                  burn_subtitles=True, show_source=False, show_target=True,
                                  end_note_enabled=True)
        started = await start_pipeline(payload)
        if started["status"] != "started":
            raise RuntimeError("Pipeline API did not start")
        running = pipeline_service._tasks[task_id]
        try:
            await start_pipeline(payload)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
        else:
            raise RuntimeError("Duplicate pipeline launch was accepted")
        try:
            await asyncio.wait_for(running, timeout=900)
        finally:
            await pipeline_service.shutdown()

    asyncio.run(run())
    task = history_manager.get_task(task_id)
    (output / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    if task.get("pipeline_status") != "completed" or task.get("progress_percent") != 100:
        raise RuntimeError("Pipeline did not complete: " + str(task.get("message")))
    original = json.loads(Path(task["transcription_path"]).read_text(encoding="utf-8"))
    translated = json.loads(Path(task["translations"]["Chinese"]).read_text(encoding="utf-8"))
    validate_alignment(original["segments"], translated["segments"])
    review = translated.get("alignment_review_rows") or []
    validate_review_rows(review, len(translated["segments"]))
    subtitle = Path(task["subtitle_outputs"]["srt"])
    if not subtitle.is_file() or not subtitle.stat().st_size:
        raise RuntimeError("Subtitle export is missing")
    video_value = task["dubbing_video_path"]
    video = settings.OUTPUT_DIR / Path(video_value).name if video_value.startswith("/static/output/") else Path(video_value)
    probe = Path(settings.FFMPEG_PATH).with_name("ffprobe.exe")
    metadata = json.loads(subprocess.check_output([str(probe), "-v", "error", "-show_streams",
                                                  "-show_format", "-of", "json", str(video)]))
    if not {"video", "audio"} <= {stream["codec_type"] for stream in metadata["streams"]}:
        raise RuntimeError("Final video lacks audio or video")
    runtime = get_qwen_runtime_status()
    if runtime.get("actual_device") != "cuda":
        raise RuntimeError("Qwen did not use CUDA: " + str(runtime))
    report = {"repo": str(args.repo.resolve()), "completed": True, "progress": 100,
              "duplicate_launch_rejected": True, "segments": len(original["segments"]),
              "timings_preserved": True, "review_rows": review, "qwen_device": "cuda",
              "video": str(video), "duration": float(metadata["format"]["duration"]),
              "subtitle": str(subtitle), "history": str(history_manager.history_file)}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
