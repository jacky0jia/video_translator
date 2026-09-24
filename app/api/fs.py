import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class ScanRequest(BaseModel):
    path: str = ""


class BrowseRequest(BaseModel):
    path: str = ""
    mode: Literal["file", "folder"] = "file"


def _existing_browse_directory(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser() if raw_path else Path.cwd()
    if candidate.is_file():
        candidate = candidate.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail="The requested folder is unavailable") from error
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="The requested path is not a folder")
    return resolved


@router.post("/fs/browse")
def browse_filesystem(request: BrowseRequest):
    """List local folders and selectable files for the in-app path picker."""
    directory = _existing_browse_directory(request.path)
    entries = []
    try:
        children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
        for child in children[:1000]:
            try:
                is_directory = child.is_dir()
                if not is_directory and (request.mode != "file" or not child.is_file()):
                    continue
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "is_directory": is_directory,
                })
            except OSError:
                continue
    except OSError as error:
        raise HTTPException(status_code=403, detail="This folder cannot be read") from error
    parent = directory.parent
    return {
        "current_path": str(directory),
        "parent_path": None if parent == directory else str(parent),
        "entries": entries,
        "truncated": len(entries) >= 1000,
    }


def _extract_size(name: str) -> str:
    match = re.search(r'whisper-(\w+)', name)
    return match.group(1) if match else ''


@router.post("/models/scan")
def scan_models(req: ScanRequest):
    """Scan a directory for faster-whisper models and return available sizes.

    Supports folders like 'whisper-base', 'faster-whisper-base', etc.
    Also scans one level deeper if no models are found at the top level.
    """
    scan_dir = Path(req.path) if req.path else None
    available = []
    is_direct_model = False

    if scan_dir and scan_dir.exists():
        # Case 2: the selected directory itself is a whisper-* model folder
        size = _extract_size(scan_dir.name)
        if size and (
            (scan_dir / "model.bin").exists() or (scan_dir / "config.json").exists()
        ):
            available.append(size)
            is_direct_model = True
        else:
            # Case 1: scan children for whisper-* subdirectories
            for entry in scan_dir.iterdir():
                if entry.is_dir():
                    size = _extract_size(entry.name)
                    if size and ((entry / "model.bin").exists() or (entry / "config.json").exists()):
                        available.append(size)
            # If none found, scan one level deeper
            if not available:
                for subdir in scan_dir.iterdir():
                    if subdir.is_dir():
                        for entry in subdir.iterdir():
                            if entry.is_dir():
                                size = _extract_size(entry.name)
                                if size and ((entry / "model.bin").exists() or (entry / "config.json").exists()):
                                    available.append(size)

    return {"models": available, "is_direct_model": is_direct_model}
