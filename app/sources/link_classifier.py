from typing import Optional
from urllib.parse import unquote, urlparse


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
    if not url:
        return None

    parsed = urlparse(url)
    path = unquote(parsed.path or "")
    if not path:
        return None

    name = path.rsplit("/", 1)[-1].strip()
    if not name:
        return None

    return name


def has_download_extension(url: str) -> bool:
    name = extract_filename_from_url(url)
    if not name:
        return False

    lowered = name.lower()
    for ext in DOWNLOAD_EXTENSIONS:
        if lowered.endswith(ext):
            return True

    return False


def is_download_candidate(url: str) -> bool:
    if not url:
        return False

    if is_ignored_scheme(url):
        return False

    if is_fragment_only(url):
        return False

    return has_download_extension(url)


def normalize_url(url: str) -> str:
    if not url:
        return url
    return url.strip()


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