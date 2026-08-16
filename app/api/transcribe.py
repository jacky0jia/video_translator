import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from app.core.config import settings
from app.core.history import history_manager
from app.core.events import emit_event
from app.core.schemas import TranscriptionResult
from app.services.asr_service import asr_service
from app.services.audio_service import audio_service
from app.services.chunking_service import chunking_service, deduplicate_segments

router = APIRouter()


async def run_transcription_task(
    task_id: str, input_source: str, filename: str, language: Optional[str]
):
    """
    Background task to handle the full transcription pipeline.
    Supports audio chunking for long videos.
    """
    audio_path = None
    chunk_paths = []
    try:
        emit_event(task_id, {"status": "extracting_audio", "message": "正在从视频中提取音频...", "progress_percent": 5})

        # 1. Extract Audio
        audio_path = await audio_service.extract_audio(input_source)

        emit_event(task_id, {"status": "transcribing", "message": "正在运行语音识别...", "progress_percent": 10})

        # 2. Keep all chunks inside one injectable ASR provider lifecycle.
        async with asr_service.stage(task_id):
            duration = chunking_service.get_audio_duration(audio_path)
            threshold_seconds = settings.CHUNKING_THRESHOLD_MINUTES * 60

            if duration > threshold_seconds:
                logging.info(
                    f"Audio duration {duration:.1f}s exceeds threshold {threshold_seconds}s, "
                    f"splitting into chunks with {settings.CHUNK_OVERLAP_SECONDS}s overlap."
                )
                emit_event(task_id, {"status": "transcribing", "message": f"音频时长 {duration:.0f} 秒，正在分块...", "progress_percent": 12})
                chunks = chunking_service.split_audio(
                    audio_path,
                    settings.CHUNKING_THRESHOLD_MINUTES,
                    settings.CHUNK_OVERLAP_SECONDS,
                )
                chunk_paths = [c[0] for c in chunks]

                all_segments = []
                detected_language = None
                total_chunks = len(chunks)
                for idx, (chunk_path, offset) in enumerate(chunks):
                    pct = 15 + int(((idx + 1) / total_chunks) * 80)
                    emit_event(task_id, {"status": "transcribing", "message": f"正在转录第 {idx + 1}/{total_chunks} 段...", "progress_percent": pct})
                    result = await asr_service.transcribe(
                        audio_path=chunk_path, language=language
                    )
                    if detected_language is None:
                        detected_language = result.language
                    # Offset timestamps back to original audio timeline
                    for seg in result.segments:
                        seg.start += offset
                        seg.end += offset
                    all_segments.extend(result.segments)

                # Deduplicate overlap regions
                all_segments = deduplicate_segments(all_segments)
                emit_event(task_id, {"status": "transcribing", "message": "转录完成，正在保存结果...", "progress_percent": 100})

                transcription_result = TranscriptionResult(
                    video_source=str(audio_path),
                    language=detected_language or "auto",
                    segments=all_segments,
                )
            else:
                # Standard single-pass transcription
                emit_event(task_id, {"status": "transcribing", "message": "正在转录音频...", "progress_percent": 50})
                transcription_result = await asr_service.transcribe(
                    audio_path=audio_path, language=language
                )
                emit_event(task_id, {"status": "transcribing", "message": "转录完成，正在保存结果...", "progress_percent": 100})

        # 3. Save as intermediate JSON
        output_filename = f"{Path(filename).stem}_transcription_{task_id}"
        output_path = asr_service.save_to_json(transcription_result, output_filename)

        # 4. Update history
        history_manager.update_task(
            task_id, {"status": "transcribed", "transcription_path": str(output_path), "language": transcription_result.language}
        )
        emit_event(task_id, {"status": "transcribed", "message": "转录完成！", "transcription_path": str(output_path), "language": transcription_result.language})

    except Exception as e:
        error_msg = str(e)
        logging.error(f"Task {task_id} failed: {error_msg}")
        history_manager.update_task(task_id, {"status": "failed", "message": error_msg})
        emit_event(task_id, {"status": "failed", "message": error_msg})
    finally:
        if audio_path:
            await audio_service.cleanup_audio(audio_path)
        if chunk_paths:
            chunking_service.cleanup_chunks(chunk_paths)


@router.post("/transcribe")
async def transcribe_video(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    local_path: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    target_lang: str = Form("Chinese"),
):
    """
    Triggers a background transcription task.
    """
    if not file and not local_path:
        raise HTTPException(
            status_code=400,
            detail="Please provide either a video file upload or a valid local path.",
        )

    processed_source = local_path
    filename = Path(local_path).name if local_path else "unknown_video"

    if file:
        # Prevent path traversal via uploaded filename
        safe_filename = os.path.basename(file.filename)
        upload_path = settings.UPLOAD_DIR / safe_filename
        settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        with open(upload_path, "wb") as buffer:
            while content := await file.read(1024 * 1024):
                buffer.write(content)
        processed_source = str(upload_path)
        filename = safe_filename

    # Create history task
    source_type = "upload" if file else "local"
    task_id = history_manager.create_task(
        filename,
        source_type,
        source_path=processed_source,
        target_lang=target_lang,
    )

    # Start background task
    background_tasks.add_task(
        run_transcription_task, task_id, processed_source, filename, language
    )

    return {
        "status": "success",
        "task_id": task_id,
        "message": "转录任务已在后台启动，请在任务列表中查看进度。",
    }
