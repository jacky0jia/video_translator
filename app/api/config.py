import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.services.hardware_service import hardware_service
from app.services.font_service import get_system_fonts
from app.services.ollama_service import OllamaService
from app.services.lm_studio_service import LMStudioService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/config")
async def get_config(request: Request):
    """Return current user-editable configuration (API key masked)."""
    client_host = request.client.host if request.client else None
    is_local = client_host in ("127.0.0.1", "::1")
    return {"config": settings.to_public_dict(), "is_local": is_local}


@router.post("/config")
async def update_config(request: Request, updates: Dict[str, Any]):
    """Update configuration fields and persist to config.yaml."""
    client_host = request.client.host if request.client else None
    is_local = client_host in ("127.0.0.1", "::1")
    if not is_local:
        # Remote users cannot modify path-related settings
        for key in ("ASR_MODEL_PATH", "FFMPEG_PATH", "LM_STUDIO_CLI_PATH"):
            updates.pop(key, None)
    try:
        settings.update(updates)
        return {"status": "success", "config": settings.to_public_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {e}")


@router.get("/hardware")
async def get_hardware_summary():
    """Return detected GPU info and recommendation for the frontend banner."""
    summary = hardware_service.get_summary()
    return {
        "gpus": summary["gpus"],
        "recommendation": summary["recommendation"],
    }


@router.get("/ollama/status")
async def get_ollama_status():
    """Check Ollama and expose installed/resident models to the settings UI."""
    service = OllamaService(settings.OLLAMA_BASE_URL)
    try:
        return {
            "healthy": True,
            "models": await service.list_models(),
            "loaded_models": await service.loaded_models(),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Ollama unavailable: {exc}")


@router.get("/lm-studio/status")
async def get_lm_studio_status():
    """List downloaded LM Studio models through the official lms CLI."""
    service = LMStudioService(settings.LM_STUDIO_CLI_PATH, settings.LM_STUDIO_PORT)
    try:
        status = await asyncio.to_thread(service.server_status)
        models = await asyncio.to_thread(service.list_models)
        runtimes = await asyncio.to_thread(service.list_runtimes)
        return {
            "healthy": True,
            "server": status,
            "models": models,
            "runtimes": runtimes,
            "runtime_ready": bool(runtimes),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LM Studio unavailable: {exc}")


@router.get("/models/asr")
async def get_asr_models():
    """Scan the models directory and return available local ASR model sizes.

    Supports folders like 'whisper-base', 'faster-whisper-base', etc.
    """
    available: List[str] = []

    def _extract_size(name: str) -> str:
        match = re.search(r'whisper-(\w+)', name)
        return match.group(1) if match else ''

    def _scan_dir(directory: Path):
        """Scan a directory for whisper-* model folders, including one level deeper."""
        found = []
        if not directory.exists() or not directory.is_dir():
            return found
        # Case 2: the directory itself is a whisper-* model folder
        size = _extract_size(directory.name)
        if size and (
            (directory / "model.bin").exists() or (directory / "config.json").exists()
        ):
            found.append(size)
            return found
        # Case 1: scan direct children for whisper-* subdirectories
        for entry in directory.iterdir():
            if entry.is_dir():
                size = _extract_size(entry.name)
                if size and ((entry / "model.bin").exists() or (entry / "config.json").exists()):
                    found.append(size)
        # If none found, scan one level deeper
        if not found:
            for subdir in directory.iterdir():
                if subdir.is_dir():
                    for entry in subdir.iterdir():
                        if entry.is_dir():
                            size = _extract_size(entry.name)
                            if size and ((entry / "model.bin").exists() or (entry / "config.json").exists()):
                                found.append(size)
        return found

    # 1. Scan custom ASR_MODEL_PATH if set
    if settings.ASR_MODEL_PATH:
        available = _scan_dir(Path(settings.ASR_MODEL_PATH))

    # 2. Fall back to default models directory
    if not available:
        available = _scan_dir(settings.BASE_DIR.parent / "models")

    return {"models": available}


@router.get("/fonts")
async def get_fonts():
    """Return installed system font families for subtitle embedding."""
    fonts = get_system_fonts()
    return {"fonts": fonts}
