"""Keep filesystem names distinct from once-encoded browser output URLs."""
from pathlib import Path
import re
from urllib.parse import quote, unquote

from app.core.config import settings


OUTPUT_PREFIX = '/static/output/'


def to_output_url(value: str | Path | None) -> str | None:
    if not value:
        return None
    value = str(value)
    if value.startswith(OUTPUT_PREFIX):
        return value  # Existing URLs are not filesystem names to encode again.
    try:
        relative = Path(value).relative_to(settings.OUTPUT_DIR)
    except ValueError:
        return value
    return OUTPUT_PREFIX + quote(relative.as_posix(), safe='/')


def output_reference_path(value: str | Path | None) -> Path | None:
    """Decode URLs once within OUTPUT_DIR; leave legacy local paths literal.

    A legacy URL with both literal-percent and decoded files is ambiguous.
    Never guess its target, especially when the caller intends to delete it.
    """
    if not value:
        return None
    value = str(value)
    if not value.startswith(OUTPUT_PREFIX):
        return Path(value)
    suffix = value[len(OUTPUT_PREFIX):]
    if (not suffix or any(char in suffix for char in ('#', '?', '\\'))
            or any(ord(char) < 32 for char in suffix)
            or re.search(r'%(?![0-9a-fA-F]{2})', suffix)):
        return None
    try:
        root = settings.OUTPUT_DIR.resolve()
        target = root / unquote(suffix, errors='strict')
        literal = root / suffix
        if not target.resolve().is_relative_to(root) or not literal.resolve().is_relative_to(root):
            return None
        if literal != target and literal.is_file() and target.exists():
            return None
        # Validate resolved containment, but preserve the final link itself:
        # cleanup must unlink an alias, never its resolved source file.
        return target
    except (OSError, ValueError, RuntimeError):
        return None
