"""V2.0 Phase 2 — Filename sanitization utilities.

Reuses the existing FileManager.safe_join mechanism rather than
duplicating path-management logic.  All sanitization happens here so
the UI and download services share one implementation.
"""

import os
import re
import unicodedata
from pathlib import Path

# Characters that are unsafe on Windows file systems.
_INVALID_WINDOWS_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*\x7f]')

# Windows reserved device names (case-insensitive, with or without extension).
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

# Fallback used when the supplied filename is empty or invalid.
_DEFAULT_FILENAME = "download"


def sanitize_filename(filename: str | None) -> str:
    """Sanitize a filename for safe use on Windows.

    - Rejects empty/None input.
    - Strips path separators and null bytes.
    - Replaces invalid Windows characters with underscores.
    - Collapses repeated underscores and dots.
    - Avoids Windows reserved device names.
    - Preserves Unicode where Windows supports it.

    The returned filename is a single path component (no directory parts).
    """
    if not filename:
        return _DEFAULT_FILENAME

    # Normalize Unicode (NFC) so accented characters are consistent.
    filename = unicodedata.normalize("NFC", filename)

    # Strip directory components — keep only the base name.
    filename = os.path.basename(filename)

    # Remove null bytes and other control characters.
    filename = _INVALID_WINDOWS_CHARS.sub("_", filename)

    # Strip leading/trailing dots and spaces (Windows ignores them).
    filename = filename.strip(". ")

    # Collapse repeated underscores (not dots — dots may be extension separators).
    filename = re.sub(r"_+", "_", filename)

    if not filename:
        return _DEFAULT_FILENAME

    # Avoid reserved device names (e.g. CON, PRN, NUL).
    stem = os.path.splitext(filename)[0].upper()
    if stem in _RESERVED_NAMES:
        return f"_{filename}"

    return filename


def split_filename_extension(filename: str) -> tuple[str, str]:
    """Split a filename into (stem, extension) safely.

    The extension is returned without the leading dot, lower-cased.
    Hidden files (e.g. `.gitignore`) are treated as having an empty stem
    and the full name as extension.
    """
    if not filename:
        return (_DEFAULT_FILENAME, "")

    path = Path(filename)
    stem = path.stem
    ext = path.suffix

    if not stem:
        # Hidden file like ".bashrc" — stem is empty.
        return (filename, "")

    return (stem, ext.lstrip(".").lower())


def is_safe_within_directory(filename: str, destination: str | Path) -> bool:
    """Verify that the sanitized filename stays inside the destination.

    Prevents path traversal and directory injection.
    """
    safe = sanitize_filename(filename)
    dest = Path(destination).resolve()
    candidate = (dest / safe).resolve()
    try:
        candidate.relative_to(dest)
    except ValueError:
        return False
    return True


def format_file_size(size: int | None) -> str:
    """Format a byte count as a human-readable string."""
    if size is None or size < 0:
        return "Unknown size"
    num = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            if unit == "B":
                return f"{num:.0f} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def file_category_label(category: str | None) -> str:
    """Map a FileCategory constant to a user-facing label."""
    labels = {
        "archive": "Archive",
        "installer": "Installer",
        "document": "Document",
        "image": "Image",
        "video": "Video",
        "audio": "Audio",
        "text": "Text",
        "binary": "Binary",
        "unknown": "Unknown",
    }
    if not category:
        return "Unknown"
    return labels.get(category.lower(), category)


__all__ = [
    "sanitize_filename",
    "split_filename_extension",
    "is_safe_within_directory",
    "format_file_size",
    "file_category_label",
]