from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from app.sources.http_headers import (
    extract_filename_from_url as _extract_filename_from_url,
    has_download_extension as _has_download_extension,
)


DOWNLOAD_EXTENSIONS = (
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


IGNORED_SCHEMES = (
    "javascript",
    "mailto",
    "tel",
    "sms",
    "data",
    "blob",
    "about",
    "file",
    "ftp",
    "ftps",
)


def is_ignored_scheme(url: str) -> bool:
    if not url:
        return True

    lowered = url.strip().lower()
    for scheme in IGNORED_SCHEMES:
        if lowered.startswith(scheme + ":"):
            return True

    if lowered.startswith("#"):
        return True

    return False


def is_fragment_only(url: str) -> bool:
    if not url:
        return True
    return url.strip().startswith("#")


def extract_filename_from_url(url: str) -> Optional[str]:
    return _extract_filename_from_url(url)


def has_download_extension(url: str) -> bool:
    return _has_download_extension(url)


def is_download_candidate(url: str) -> bool:
    if not url:
        return False

    if is_ignored_scheme(url):
        return False

    if is_fragment_only(url):
        return False

    return has_download_extension(url)


def deduplicate_preserve_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def normalize_url(url: str) -> str:
    """Conservative URL normalization for deduplication.

    - lowercases scheme + host
    - removes fragment
    - removes default ports (80 for http, 443 for https)
    - normalizes empty path to "/"
    - preserves path, query parameters (different ?id= must stay distinct)

    Does NOT:
    - re-sort query parameters
    - drop meaningful query keys
    - follow redirects
    """
    if not url:
        return url

    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return url

    host = (parts.hostname or "").lower()
    if not host:
        return url

    port = parts.port
    if port is not None:
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            port = None
        netloc = host if port is None else f"{host}:{port}"
    else:
        netloc = host

    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
        if not path:
            path = "/"

    query = parts.query or ""
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    return normalized