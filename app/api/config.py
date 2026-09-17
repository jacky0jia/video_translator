import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
import httpx
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.settings_schema import SETTINGS_BY_KEY, get_settings_schema
from app.services.hardware_service import hardware_service
from app.services.font_service import get_system_fonts
from app.services.ollama_service import OllamaService
from app.services.lm_studio_service import LMStudioService
from app.services.tts.lm_studio_probe import LMStudioTTSProbe, LMStudioTTSProbeResult
from app.services.tts.qwen_installer import (
    QwenTTSInstallError,
    assemble_qwen_tts_bundle,
    inspect_qwen_tts_installation,
    preflight_qwen_tts_bundle,
)
from app.services.tts.qwen_voices import QwenVoiceCatalog

logger = logging.getLogger(__name__)
router = APIRouter()
_qwen_install_lock = asyncio.Lock()

_QWEN_MODEL_NAME = "Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf"
_QWEN_MMPROJ_NAME = "mmproj-Qwen3-TTS-12Hz-1.7B-Base-bf16.gguf"
_QWEN_MODEL_SHA256 = "8d18c94acb2addd042f97da63c98be144eafa76d0d9495177eab65130cf85129"
_QWEN_MMPROJ_SHA256 = "b9503e95e44705739cf82c15ce909b040b96a31ad79d02cf4dc5e1484399265d"


class LMStudioTTSCapabilityResponse(BaseModel):
    available: bool
    code: str
    detail: str
    status_code: Optional[int] = None
    media_type: str = ""


class QwenVoiceCatalogResponse(BaseModel):
    available: bool
    count: int = Field(ge=0)
    languages: List[str]
    presentations: List[str]


class LMStudioStatusResponse(BaseModel):
    healthy: bool
    diagnostic_errors: List[str]
    server: Dict[str, Any]
    models: List[str]
    runtimes: List[str]
    runtime_ready: Optional[bool] = None
    qwen_tts_models: List[str]
    qwen_tts_compatible_models: List[str]
    tts_capability: LMStudioTTSCapabilityResponse
    qwen_voice_catalog: QwenVoiceCatalogResponse


class QwenTTSInstallRequest(BaseModel):
    llama_archive: str = Field(min_length=1)
    cuda_archive: Optional[str] = None
    model_directory: str = Field(min_length=1)
    device: str = Field(default="cuda", pattern="^(cuda|vulkan|cpu)$")


def _qwen_install_paths(payload: QwenTTSInstallRequest) -> tuple[Path, dict]:
    app_root = settings.BASE_DIR.parent
    destination = app_root / "models" / "qwen3-tts"
    model_directory = Path(payload.model_directory)
    if payload.device != "vulkan" and not payload.cuda_archive:
        raise QwenTTSInstallError("CUDA redistribution archive is required")
    manifest_name = (
        "runtime-manifest-vulkan.b10792.json"
        if payload.device == "vulkan" else "runtime-manifest.b10792.json"
    )
    return destination, {
        "llama_archive": payload.llama_archive,
        "cuda_archive": None if payload.device == "vulkan" else payload.cuda_archive,
        "model_path": model_directory / _QWEN_MODEL_NAME,
        "mmproj_path": model_directory / _QWEN_MMPROJ_NAME,
        "destination": destination,
        "manifest_path": app_root / "packaging" / "llama-tts" / manifest_name,
        "device": payload.device,
    }


def _is_local_request(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    return client_host in ("127.0.0.1", "::1")


@router.get("/config")
async def get_config(request: Request):
    """Return current user-editable configuration (API key masked)."""
    is_local = _is_local_request(request)
    from app.services.upstream_dependencies import upstream_dependency_status
    result = {"config": settings.to_public_dict(), "is_local": is_local}
    if is_local:
        status = upstream_dependency_status()
        if status['user_install']:
            result['upstream_dependencies'] = status
    return result


@router.get('/dependencies/install-guide')
async def get_upstream_install_guide(request: Request):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail='Local access only')
    guide = settings.BASE_DIR.parent / 'README-PORTABLE.md'
    if not guide.is_file():
        guide = settings.BASE_DIR.parent / 'README-PORTABLE.zh-CN.md'
    if not (guide.parent / 'upstream-dependencies.json').is_file() or not guide.is_file():
        raise HTTPException(status_code=404, detail='Upstream installation guide is unavailable')
    return FileResponse(guide, media_type='text/plain; charset=utf-8')


@router.get("/config/schema")
async def get_config_schema(request: Request):
    """Return typed UI metadata without exposing local-only fields remotely."""
    return get_settings_schema(is_local=_is_local_request(request))


@router.post("/config")
async def update_config(request: Request, updates: Dict[str, Any]):
    """Update configuration fields and persist to config.yaml."""
    if not _is_local_request(request):
        for key, definition in SETTINGS_BY_KEY.items():
            if definition.local_only:
                updates.pop(key, None)
    try:
        settings.update(updates)
        return {"status": "success", "config": settings.to_public_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save configuration: {e}")


@router.post("/qwen-tts/install")
async def install_qwen_tts(request: Request, payload: QwenTTSInstallRequest):
    """Assemble the pinned private bundle from files already on the local machine."""
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Qwen3-TTS installation is local-only")
    if payload.device == "vulkan" and hardware_service.amd_info is None:
        raise HTTPException(status_code=400, detail="AMD Vulkan device is not available")
    try:
        destination, arguments = _qwen_install_paths(payload)
    except QwenTTSInstallError as exc:
        raise HTTPException(status_code=400, detail=f"Qwen3-TTS installation failed: {exc}")
    app_root = settings.BASE_DIR.parent
    arguments.update({
        "model_sha256": _QWEN_MODEL_SHA256,
        "mmproj_sha256": _QWEN_MMPROJ_SHA256,
        "llama_license_path": app_root / "packaging" / "llama-tts" / "LICENSE-llama.cpp",
        "notice_path": app_root / "packaging" / "llama-tts" / "NOTICE.md",
    })
    try:
        async with _qwen_install_lock:
            await asyncio.to_thread(assemble_qwen_tts_bundle, **arguments)
    except asyncio.CancelledError:
        raise
    except QwenTTSInstallError as exc:
        logger.warning("Qwen3-TTS installation rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail=str(exc))
    except (OSError, ValueError) as exc:
        logger.warning("Qwen3-TTS installation rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail="Qwen3-TTS source files are unavailable")
    return {
        "status": "success",
        "model": "Qwen3-TTS 1.7B Base Q4_K_M",
        **inspect_qwen_tts_installation(destination),
    }


@router.post("/qwen-tts/preflight")
async def preflight_qwen_tts(request: Request, payload: QwenTTSInstallRequest):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Qwen3-TTS installation is local-only")
    if payload.device == "vulkan" and hardware_service.amd_info is None:
        raise HTTPException(status_code=400, detail="AMD Vulkan device is not available")
    try:
        _destination, arguments = _qwen_install_paths(payload)
        return await asyncio.to_thread(preflight_qwen_tts_bundle, **arguments)
    except (OSError, ValueError, QwenTTSInstallError) as exc:
        logger.warning("Qwen3-TTS preflight rejected: %s", type(exc).__name__)
        detail = str(exc) if isinstance(exc, QwenTTSInstallError) else "Source files are unavailable"
        raise HTTPException(status_code=400, detail=f"Qwen3-TTS preflight failed: {detail}")


@router.get("/qwen-tts/install-status")
async def get_qwen_tts_install_status(request: Request):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Qwen3-TTS installation status is local-only")
    try:
        result = inspect_qwen_tts_installation(settings.BASE_DIR.parent / "models" / "qwen3-tts")
        from app.services.upstream_dependencies import qwen_upstream_sources
        sources = qwen_upstream_sources()
        if sources:
            result['upstream_sources'] = sources
        return result
    except QwenTTSInstallError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/qwen-tts/runtime-status")
async def get_qwen_tts_runtime_status(request: Request):
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail="Qwen3-TTS runtime status is local-only")
    from app.services.tts.qwen import get_qwen_runtime_status
    return get_qwen_runtime_status()


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


@router.get("/lm-studio/status", response_model=LMStudioStatusResponse)
async def get_lm_studio_status():
    """List downloaded LM Studio models through the official lms CLI."""
    service = LMStudioService(settings.LM_STUDIO_CLI_PATH, settings.LM_STUDIO_PORT)
    catalog_root = settings.BASE_DIR.parent / "models" / "voices" / "librivox_public_domain"
    try:
        status_result, models_result, runtimes_result, catalog_result = await asyncio.gather(
            asyncio.to_thread(service.server_status),
            asyncio.to_thread(service.list_model_details),
            asyncio.to_thread(service.list_runtimes),
            asyncio.to_thread(lambda: QwenVoiceCatalog(catalog_root).list()),
            return_exceptions=True,
        )
        for result in (status_result, models_result, runtimes_result, catalog_result):
            if isinstance(result, asyncio.CancelledError):
                raise result
        diagnostic_errors = [
            name
            for name, result in (
                ("server", status_result),
                ("models", models_result),
                ("runtimes", runtimes_result),
            )
            if isinstance(result, BaseException)
        ]
        if len(diagnostic_errors) == 3:
            raise RuntimeError("All LM Studio diagnostics failed")
        status = {} if isinstance(status_result, BaseException) else status_result
        model_details = [] if isinstance(models_result, BaseException) else models_result
        runtimes = [] if isinstance(runtimes_result, BaseException) else runtimes_result
        models = [str(item["modelKey"]) for item in model_details]
        qwen_tts_details = [
            item for item in model_details
            if "qwen3-tts" in str(item.get("modelKey", "")).lower()
        ]
        qwen_tts_models = [str(item["modelKey"]) for item in qwen_tts_details]
        compatible_qwen_models = [
            str(item["modelKey"])
            for item in qwen_tts_details
            if str(item.get("format", "")).lower() == "gguf"
            and str(item.get("architecture", "")).lower() == "qwen3tts"
            and "1.7b-base" in str(item.get("modelKey", "")).lower()
        ]
        if compatible_qwen_models:
            probe = LMStudioTTSProbe(
                settings.LM_STUDIO_BASE_URL,
                client_factory=httpx.AsyncClient,
                api_key=settings.LLM_API_KEY,
            )
            tts_capability = await probe.probe(compatible_qwen_models[0])
        elif qwen_tts_models:
            tts_capability = LMStudioTTSProbeResult(
                False,
                "incompatible_model",
                "Installed Qwen3-TTS model is not the required 1.7B Base GGUF variant",
            )
        else:
            tts_capability = LMStudioTTSProbeResult(
                False,
                "model_not_found",
                "No Qwen3-TTS model is installed in LM Studio",
            )
        if not isinstance(catalog_result, BaseException):
            catalog_voices = catalog_result
            voice_catalog = {
                "available": True,
                "count": len(catalog_voices),
                "languages": sorted({voice.language for voice in catalog_voices}),
                "presentations": sorted({voice.presentation for voice in catalog_voices}),
            }
        else:
            logger.warning("Qwen preset voice catalog is unavailable")
            voice_catalog = {
                "available": False,
                "count": 0,
                "languages": [],
                "presentations": [],
            }
        return {
            "healthy": not diagnostic_errors,
            "diagnostic_errors": diagnostic_errors,
            "server": status,
            "models": models,
            "runtimes": runtimes,
            "runtime_ready": None if "runtimes" in diagnostic_errors else bool(runtimes),
            "qwen_tts_models": qwen_tts_models,
            "qwen_tts_compatible_models": compatible_qwen_models,
            "tts_capability": {
                "available": tts_capability.available,
                "code": tts_capability.code,
                "detail": tts_capability.detail,
                "status_code": tts_capability.status_code,
                "media_type": tts_capability.media_type,
            },
            "qwen_voice_catalog": voice_catalog,
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
