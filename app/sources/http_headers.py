"""Shared, dependency-free helpers for parsing HTTP header data.

Kept in one place so that:
- DirectDownloadAdapter
- ResourceProbe
- future adapters
all stay consistent.
"""
import os
from typing import Optional
from urllib.parse import unquote, urlparse


_DOWNLOADABLE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".iso", ".img",
    ".exe", ".msi", ".deb", ".rpm", ".dmg", ".apk", ".pkg",
    ".pdf",
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",
    ".mp3", ".flac", ".wav", ".ogg", ".m4a",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
    ".txt", ".csv", ".json", ".xml",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)


_NON_DOWNLOADABLE_MEDIA = frozenset({
    "text/html",
    "application/xhtml+xml",
    "text/xml",
    "application/xml",
})


_DOWNLOADABLE_MEDIA_PREFIXES = (
    "application/zip",
    "application/x-zip-compressed",
    "application/x-rar-compressed",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
    "application/pdf",
    "application/octet-stream",
    "application/x-msdownload",
    "application/x-msi",
    "application/vnd.debian.binary-package",
    "application/x-rpm",
    "application/x-apple-diskimage",
    "application/vnd.android.package-archive",
    "application/x-deb",
    "application/x-dosexec",
    "application/x-executable",
    "image/",
    "video/",
    "audio/",
)


_DOWNLOADABLE_EXACT = frozenset({
    "application/zip",
    "application/x-zip-compressed",
    "application/x-rar-compressed",
    "application/x-7z-compressed",
    "application/x-tar",
    "application/gzip",
    "application/pdf",
    "application/octet-stream",
    "application/x-msdownload",
    "application/x-msi",
    "application/vnd.debian.binary-package",
    "application/x-rpm",
    "application/x-apple-diskimage",
    "application/vnd.android.package-archive",
    "application/x-deb",
    "application/x-dosexec",
    "application/x-executable",
})


def safe_content_length(raw: Optional[str]) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value or None


def extract_filename_from_content_disposition(content_disposition: str | None) -> Optional[str]:
    if not content_disposition:
        return None

    filename_star: Optional[str] = None
    filename_plain: Optional[str] = None

    for part in content_disposition.split(";"):
        part = part.strip()
        lower = part.lower()

        if lower.startswith("filename*="):
            value = part[len("filename*="):].strip().strip('"')
            if "''" in value:
                value = value.split("''", 1)[1]
            value = unquote(value)
            if value:
                filename_star = os.path.basename(value)
        elif lower.startswith("filename="):
            value = part[len("filename="):].strip().strip('"')
            if value:
                filename_plain = os.path.basename(value)

    if filename_star:
        return filename_star
    if filename_plain:
        return filename_plain
    return None


def extract_filename_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    path = unquote(urlparse(url).path or "")
    if not path:
        return None
    name = path.rsplit("/", 1)[-1].strip()
    return name or None


def normalize_media_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    media = content_type.split(";", 1)[0].strip().lower()
    return media or None


def has_download_extension(url: str) -> bool:
    name = extract_filename_from_url(url)
    if not name:
        return False
    lowered = name.lower()
    return any(lowered.endswith(ext) for ext in _DOWNLOADABLE_EXTENSIONS)


def classify_downloadability(
    *,
    media_type: str | None,
    url: str,
    status_code: int,
) -> bool:
    if status_code >= 400:
        return False

    if media_type:
        if media_type in _NON_DOWNLOADABLE_MEDIA:
            return False
        if media_type in _DOWNLOADABLE_EXACT:
            return True
        for prefix in _DOWNLOADABLE_MEDIA_PREFIXES:
            if media_type.startswith(prefix):
                return True
        return False

    return has_download_extension(url)


def parse_accept_ranges(raw: str | None) -> Optional[bool]:
    if raw is None:
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if value == "bytes":
        return True
    if value == "none":
        return False
    return None