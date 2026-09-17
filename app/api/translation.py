import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.history import history_manager
from app.core.events import emit_event
from app.core.schemas import TranscriptionResult, TranscriptionSegment
from app.services.formatter_service import subtitle_formatter
from app.services.translation_service import translation_service

router = APIRouter()

LANG_CODE_MAP = {
    'chinese': ['zh', 'zh-cn', 'zh-tw', 'chinese', '中文'],
    'english': ['en', 'english', 'eng'],
    'japanese': ['ja', 'jp', 'japanese', '日语', '日本語'],
    'korean': ['ko', 'korean', '韩语', '한국어'],
}


def is_same_language(detected_lang: str, target_lang: str) -> bool:
    if not detected_lang or not target_lang:
        return False
    detected = detected_lang.lower().strip()
    target = target_lang.lower().strip()
    if detected == target:
        return True
    for aliases in LANG_CODE_MAP.values():
        if target in aliases:
            return detected in aliases
    return False


def _resolve_fs_path(path_str: Optional[str]) -> Optional[Path]:
    """Convert a URL path to a filesystem Path within OUTPUT_DIR."""
    if not path_str:
        return None
    if path_str.startswith("/static/output/"):
        rel = path_str[len("/static/output/"):].replace("/", os.sep)
        target = settings.OUTPUT_DIR / rel
    else:
        target = settings.OUTPUT_DIR / path_str

    try:
        resolved = target.resolve(strict=False)
        resolved.relative_to(settings.OUTPUT_DIR.resolve())
        if resolved.exists():
            return resolved
    except (ValueError, RuntimeError, OSError):
        pass
    return None


@router.post("/translate")
async def translate_transcription(
    json_path: str = Form(...), target_lang: str = Form("Chinese"), task_id: Optional[str] = Form(None),
    pipeline: bool = False,
):
    """
    Translates an existing transcription JSON file using LLM with sliding window.
    If task_id is provided, resolves the real file path from history manager.
    """
    resolved_path = json_path
    if task_id:
        task = history_manager.get_task(task_id)
        if task and task.get("transcription_path"):
            resolved_path = task["transcription_path"]

    path = Path(resolved_path)
    if not path.exists():
        raise HTTPException(
            status_code=404, detail="Transcription JSON file not found."
        )

    try:
        if task_id:
            history_manager.update_task(
                task_id, {"status": "translating"}
            )
            emit_event(task_id, {"status": "translating", "message": "started", "target_lang": target_lang, "progress_percent": 5})

        # 1. Load the original transcription result
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            result = TranscriptionResult(**data)

        # 2. Perform translation using sliding window
        if is_same_language(result.language, target_lang):
            translated_result = TranscriptionResult(
                video_source=result.video_source,
                language=target_lang,
                segments=[
                    TranscriptionSegment(
                        start=seg.start,
                        end=seg.end,
                        text=seg.text,
                        confidence=seg.confidence,
                    )
                    for seg in result.segments
                ],
            )
            if task_id:
                emit_event(task_id, {"status": "translating", "message": "skipped", "target_lang": target_lang, "progress_percent": 95})
        else:
            async def _progress_callback(batch_done, total_batches):
                pct = 5 + int((batch_done / total_batches) * 90)
                progress = {
                    "status": "translating",
                    "message": f"{batch_done}/{total_batches}",
                    "target_lang": target_lang,
                    "progress_percent": pct,
                }
                # SSE may reconnect or the page may reload during a long
                # translation. Persist the same progress that we emit so the
                # task does not appear stuck at provider model switching.
                history_manager.update_task(task_id, progress)
                emit_event(task_id, progress)

            translated_result = await translation_service.translate_full_result(
                result=result, target_lang=target_lang,
                progress_callback=_progress_callback if task_id else None,
                task_id=task_id,
                # The integrated pipeline is the dubbing workflow. Its visible
                # translation is also the spoken script, so fit it before TTS.
                # Standalone subtitle translation keeps the faithful-text mode.
                fit_to_duration=pipeline,
            )

        # 3. Check for translation failures
        failure_markers = {"Translation error", "[Translation failed]"}
        failed_segments = [
            index for index, seg in enumerate(translated_result.segments)
            if not seg.text.strip() or seg.text.strip() in failure_markers
        ]
        if failed_segments:
            err_msg = (
                "Translation failed for segment(s): "
                + ", ".join(str(index + 1) for index in failed_segments)
                + ". The previous valid translation was kept."
            )
            if task_id:
                history_manager.update_task(
                    task_id, {"status": "translation_failed", "message": err_msg}
                )
                emit_event(task_id, {"status": "translation_failed", "message": err_msg})
            raise RuntimeError(err_msg)

        # 4. Save the translated result as a new JSON file (language-specific)
        output_filename = f"{path.stem}_translated_{target_lang}_{task_id or 'standalone'}"
        output_path = settings.OUTPUT_DIR / f"{output_filename}.json"
        settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(translated_result.model_dump_json(indent=4))

        # 5. Update history if task_id is provided
        if task_id:
            task = history_manager.get_task(task_id) or {}
            translations = dict(task.get("translations") or {})
            translations[target_lang] = str(output_path)
            translation_profiles = dict(task.get("translation_profiles") or {})
            translation_profiles[target_lang] = "dubbing" if pipeline else "subtitles"
            pipeline_translation_targets = list(task.get("pipeline_translation_targets") or [])
            if pipeline and target_lang not in pipeline_translation_targets:
                pipeline_translation_targets.append(target_lang)
            final_status = "pipeline_translated" if pipeline else "completed"
            history_manager.update_task(
                task_id,
                {
                    "status": final_status,
                    "translation_path": str(output_path),
                    "target_lang": target_lang,
                    "translations": translations,
                    "translation_profiles": translation_profiles,
                    "pipeline_translation_targets": pipeline_translation_targets,
                },
            )
            emit_event(task_id, {"status": final_status, "message": "completed", "target_lang": target_lang})

        return {
            "status": "success",
            "translated_file": str(output_path),
            "target_language": target_lang,
        }

    except Exception as e:
        err_msg = str(e)
        if task_id:
            history_manager.update_task(task_id, {"status": "translation_failed", "message": err_msg})
            emit_event(task_id, {"status": "translation_failed", "message": err_msg})
        raise HTTPException(status_code=500, detail=f"Translation failed: {err_msg}")


@router.post("/export")
async def export_subtitles(
    json_path: str = Form(...),
    format: str = Form("srt"),  # srt, vtt, ass
    task_id: Optional[str] = Form(None),
    mode: str = Form("translated"),  # translated, original, bilingual
    extra_json_paths: Optional[str] = Form(None),
    source_color: Optional[str] = Form(None),
    target_color: Optional[str] = Form(None),
    font_size: Optional[int] = Form(None),
    offset_y: Optional[int] = Form(None),
    bold: Optional[bool] = Form(None),
    font_family: Optional[str] = Form(None),
    download: bool = Form(True),
    process_route: str = Form("subtitles"),
    burn_enabled: bool = Form(False),
):
    """
    Exports a transcription JSON to a specific subtitle format.
    mode: translated (default), original, bilingual
    extra_json_paths: comma-separated list of additional translation JSON paths for multilingual export
    Returns the file as a downloadable response.
    """
    task = None
    if task_id:
        task = history_manager.get_task(task_id)

    # Resolve paths based on mode
    resolved_path = json_path
    original_path = None

    if task:
        if mode == "original" and task.get("transcription_path"):
            resolved_path = task["transcription_path"]
        elif task.get("translation_path"):
            resolved_path = task["translation_path"]
        if mode == "bilingual" and task.get("transcription_path"):
            original_path = task["transcription_path"]

    path = _resolve_fs_path(resolved_path)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="JSON file not found.")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            result = TranscriptionResult(**data)

        original_result = None
        if original_path:
            orig_fs = _resolve_fs_path(original_path)
            if orig_fs and orig_fs.exists():
                with open(orig_fs, "r", encoding="utf-8") as f:
                    orig_data = json.load(f)
                    original_result = TranscriptionResult(**orig_data)

        # Load extra translations for multilingual export
        extra_results = []
        if extra_json_paths:
            for ep in extra_json_paths.split(","):
                ep = ep.strip()
                if not ep:
                    continue
                ep_fs = _resolve_fs_path(ep)
                if ep_fs and ep_fs.exists():
                    with open(ep_fs, "r", encoding="utf-8") as f:
                        extra_data = json.load(f)
                        extra_results.append(TranscriptionResult(**extra_data))

        has_extra = len(extra_results) > 0

        # 1. Format content based on chosen type
        if format.lower() == "srt":
            content = subtitle_formatter.to_srt(result, original_result, extra_results)
            ext = "srt"
        elif format.lower() == "vtt":
            content = subtitle_formatter.to_vtt(result, original_result, extra_results)
            ext = "vtt"
        elif format.lower() == "ass":
            content = subtitle_formatter.to_ass(
                result, original_result, extra_results,
                source_color=source_color, target_color=target_color,
                font_size=font_size, offset_y=offset_y,
                bold=bold, font_family=font_family,
            )
            ext = "ass"
        else:
            raise HTTPException(
                status_code=400, detail="Unsupported format. Use srt, vtt, or ass."
            )

        # 2. Save to file
        if has_extra:
            suffix = "_multilingual"
        else:
            suffix = f"_{mode}" if mode in ("original", "bilingual") else ""
        target_lang = task.get("target_lang", "") if task else ""
        filename = path.stem.replace("_translated", "").replace(f"_{target_lang}", "") + suffix
        out_path = subtitle_formatter.save_to_file(content, filename, ext)

        output_url = f"/static/output/{out_path.relative_to(settings.OUTPUT_DIR).as_posix()}"
        if task_id and task:
            subtitle_outputs = dict(task.get("subtitle_outputs") or {})
            subtitle_outputs[ext] = str(out_path)
            history_manager.update_task(
                task_id,
                {
                    "subtitle_outputs": subtitle_outputs,
                    "last_subtitle_format": ext,
                    "last_process_route": "dubbing" if process_route == "dubbing" else "subtitles",
                    "last_burn_enabled": bool(burn_enabled) if process_route != "dubbing" else False,
                },
            )

        if not download:
            return {
                "status": "success",
                "format": ext,
                "filename": f"{filename}.{ext}",
                "output_path": output_url,
            }

        # 3. Return as downloadable file
        media_types = {
            "srt": "text/plain",
            "vtt": "text/vtt",
            "ass": "text/plain",
        }
        return FileResponse(
            out_path,
            filename=f"{filename}.{ext}",
            media_type=media_types.get(ext, "application/octet-stream"),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")
