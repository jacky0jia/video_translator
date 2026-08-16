import logging
import platform
import re
import subprocess
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


class ScanRequest(BaseModel):
    path: str = ""


def _tkinter_select_folder() -> str:
    """Use tkinter to open a native folder dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        root.lift()
        path = filedialog.askdirectory(title="Select a folder")
        root.destroy()
        if path:
            return str(Path(path))
    except Exception as e:
        logger.warning(f"tkinter folder dialog failed: {e}")
    return ""


def _tkinter_select_file() -> str:
    """Use tkinter to open a native file dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        root.lift()
        path = filedialog.askopenfilename(title="Select a file")
        root.destroy()
        if path:
            return str(Path(path))
    except Exception as e:
        logger.warning(f"tkinter file dialog failed: {e}")
    return ""


def _zenity_select_folder() -> str:
    try:
        result = subprocess.run(
            ["zenity", "--file-selection", "--directory"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _zenity_select_file() -> str:
    try:
        result = subprocess.run(
            ["zenity", "--file-selection"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout.strip()
    except Exception:
        return ""


@router.post("/fs/select-folder")
def select_folder():
    """Open a system folder browser dialog and return the selected absolute path."""
    selected = _tkinter_select_folder()
    if not selected and platform.system() != "Windows":
        selected = _zenity_select_folder()
    if not selected:
        return {"path": None}
    return {"path": selected}


@router.post("/fs/select-file")
def select_file():
    """Open a system file browser dialog and return the selected absolute path."""
    selected = _tkinter_select_file()
    if not selected and platform.system() != "Windows":
        selected = _zenity_select_file()
    if not selected:
        return {"path": None}
    return {"path": selected}


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
