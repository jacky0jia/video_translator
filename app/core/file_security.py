"""File boundaries for the single-user media application."""
from pathlib import Path
import re
import uuid

from fastapi import HTTPException, UploadFile

MEDIA_EXTENSIONS = frozenset({'.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.mpeg', '.mpg', '.wmv', '.flv', '.ts', '.mts', '.m2ts', '.3gp', '.ogv', '.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus'})
AUDIO_EXTENSIONS = frozenset({'.wav', '.mp3', '.flac', '.ogg', '.m4a'})
MAX_MEDIA_BYTES = 8 * 1024**3
MAX_CLONE_BYTES = 64 * 1024**2


def upload_name(filename: str | None, extensions: frozenset[str]) -> str:
    # Recognize both Windows and POSIX separators regardless of host OS.
    name = (filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    if not name or any(ord(c) < 32 for c in name) or ':' in name:
        raise HTTPException(400, 'Invalid media filename')
    suffix = Path(name).suffix.lower()
    if suffix not in extensions:
        raise HTTPException(400, 'Unsupported media format')
    stem = re.sub(r'[^\w .-]', '_', Path(name).stem, flags=re.UNICODE).strip(' .')[:120] or 'media'
    return stem + suffix


def validate_file_component(value: str, label: str) -> str:
    if not value or len(value) > 64 or any(not (c.isalnum() or c in ' _-') for c in value):
        raise HTTPException(400, f'Invalid {label}')
    return value


def contained_file(root: Path, value: str) -> Path | None:
    try:
        path = (root / value).resolve()
        if path.is_relative_to(root.resolve()) and path.is_file():
            return path
    except (OSError, ValueError, RuntimeError):
        pass
    return None


async def save_upload(file: UploadFile, directory: Path, extensions: frozenset[str], limit: int) -> tuple[Path, str]:
    name = upload_name(file.filename, extensions)
    directory.mkdir(parents=True, exist_ok=True)
    # The name on disk is independent of the displayed filename. Never overwrite.
    destination = directory / (uuid.uuid4().hex + Path(name).suffix)
    written = 0
    try:
        with destination.open('xb') as stream:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, 'Media file exceeds the upload size limit')
                stream.write(chunk)
        if written == 0:
            raise HTTPException(400, 'Media file is empty')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination, name
