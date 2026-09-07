"""Pure resource categorization for V1.9.

Categorization answers the question *"what kind of resource is this?"* It
is intentionally separate from filtering (which asks *"which resources?"*).

Hard rules:
  * Pure Python — no PySide6, no I/O, no resolver/probe calls.
  * Inspects only `ResourceView.file` (name, url, content_type).
  * Order-of-precedence: PART > PATCH > INSTALLER > ARCHIVE > DOCUMENTATION > UNKNOWN.
  * Deterministic — same input always returns the same category.
  * Non-mutating — never mutates the input.
  * Safe for missing filename / content_type.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from app.services.analysis_view import ResourceView


class ResourceCategory(str, Enum):
    INSTALLER = "installer"
    ARCHIVE = "archive"
    PART = "part"
    PATCH = "patch"
    DOCUMENTATION = "documentation"
    UNKNOWN = "unknown"


INSTALLER_EXTENSIONS = {
    "exe", "msi", "dmg", "pkg", "deb", "rpm", "apk", "appimage",
    "run", "jar",
}

ARCHIVE_EXTENSIONS = {
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "txz", "lz", "lzma", "z",
}

DOCUMENTATION_EXTENSIONS = {
    "pdf", "txt", "md", "rtf", "doc", "docx", "odt",
    "epub", "mobi",
    "chm",
    "html", "htm",
}

PART_PATTERNS = [
    re.compile(r"\.(part|r(?:ar)?|z)(\d+)\.[^.]+$", re.IGNORECASE),
    re.compile(r"\.(part|r(?:ar)?|z)(\d+)$", re.IGNORECASE),
    re.compile(r"\.part0*\d+", re.IGNORECASE),
    re.compile(r"\.r0*\d+", re.IGNORECASE),
    re.compile(r"\.z0*\d+", re.IGNORECASE),
    re.compile(r"\.zip\.0*\d+$", re.IGNORECASE),
    re.compile(r"-0*\d+\.(zip|rar|7z|tar|gz)$", re.IGNORECASE),
]

PATCH_KEYWORDS = (
    "patch", "update", "updater", "hotfix", "delta",
    "servicepack", "sp", "upgrade", "incremental",
)

LAUNCHER_KEYWORDS = (
    "launcher", "boot", "bootstrap",
)

_DOC_HINTS = (
    "readme", "manual", "guide", "license", "changelog",
    "release-notes", "release_notes", "notes", "help",
    "instructions", "eula",
)


def _extension(name: Optional[str]) -> str:
    if not name:
        return ""
    dot = name.rfind(".")
    if dot == -1 or dot == len(name) - 1:
        return ""
    return name[dot + 1:].lower()


def _stem(name: Optional[str]) -> str:
    if not name:
        return ""
    slash = name.rfind("/")
    base = name[slash + 1:] if slash != -1 else name
    dot = base.rfind(".")
    return base[:dot] if dot > 0 else base


def _looks_like_part(name: Optional[str]) -> bool:
    if not name:
        return False
    base = _stem(name)
    if not base:
        return False
    candidate = name.lower()
    for pattern in PART_PATTERNS:
        if pattern.search(candidate):
            return True
    multipart_prefix = re.match(r"^(.+?)[._ -](part|r)(\d+)$", base, re.IGNORECASE)
    if multipart_prefix:
        return True
    multipart_name = re.match(r"^(.+?)\.(\d{2,3})$", base)
    if multipart_name and _extension(name) in (ARCHIVE_EXTENSIONS | {"iso", "bin", "img"}):
        return True
    vol_ext = re.search(r"\.(rar|zip|7z|tar|gz)\.(\d{2,4})$", candidate)
    if vol_ext:
        return True
    r_vol = re.search(r"\.r(\d{2,3})$", candidate)
    if r_vol and _extension(base) in ARCHIVE_EXTENSIONS:
        return True
    return False


def _looks_like_patch(name: Optional[str]) -> bool:
    if not name:
        return False
    stem = _stem(name).lower()
    if not stem:
        return False
    for kw in PATCH_KEYWORDS:
        if kw in stem:
            ext = _extension(name)
            if ext in INSTALLER_EXTENSIONS or ext in ARCHIVE_EXTENSIONS or ext in {"iso", "bin", "pkg"}:
                return True
            if ext == "":
                return True
    return False


def _looks_like_launcher(name: Optional[str]) -> bool:
    if not name:
        return False
    stem = _stem(name).lower()
    return any(kw in stem for kw in LAUNCHER_KEYWORDS)


def _looks_like_documentation(name: Optional[str], content_type: Optional[str]) -> bool:
    if not name and not content_type:
        return False
    ext = _extension(name)
    if ext in DOCUMENTATION_EXTENSIONS:
        return True
    stem = (_stem(name) or "").lower()
    if any(hint in stem for hint in _DOC_HINTS):
        if ext in DOCUMENTATION_EXTENSIONS or ext in {"", "txt", "md"}:
            return True
    if content_type:
        media = content_type.split(";", 1)[0].strip().lower()
        if media in {
            "text/plain", "text/markdown", "text/html",
            "application/pdf", "application/epub+zip",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.oasis.opendocument.text",
            "application/rtf",
        }:
            return True
    return False


def _looks_like_installer(name: Optional[str], ext: str, content_type: Optional[str]) -> bool:
    if ext in INSTALLER_EXTENSIONS:
        if _looks_like_patch(name):
            return False
        if _looks_like_part(name):
            return False
        return True
    if content_type:
        media = content_type.split(";", 1)[0].strip().lower()
        if media in {
            "application/x-msi", "application/vnd.android.package-archive",
            "application/x-deb", "application/x-rpm",
            "application/x-apple-diskimage",
            "application/java-archive",
        }:
            return True
    return False


def _looks_like_archive(ext: str, content_type: Optional[str]) -> bool:
    if ext in ARCHIVE_EXTENSIONS:
        return True
    if content_type:
        media = content_type.split(";", 1)[0].strip().lower()
        if media in {
            "application/zip", "application/x-zip-compressed",
            "application/x-rar-compressed", "application/vnd.rar",
            "application/x-7z-compressed", "application/x-tar",
            "application/gzip", "application/x-gzip",
            "application/x-bzip2", "application/x-xz",
        }:
            return True
    return False


def categorize_resource(view: ResourceView) -> ResourceCategory:
    """Return a single `ResourceCategory` for the given resource.

    Precedence: PART > PATCH > INSTALLER > ARCHIVE > DOCUMENTATION > UNKNOWN.
    """
    file_obj = view.file
    name = file_obj.name
    url = file_obj.url or ""
    content_type = file_obj.content_type

    candidate_name = name or url
    ext = _extension(candidate_name)

    if _looks_like_part(candidate_name):
        return ResourceCategory.PART
    if _looks_like_patch(candidate_name):
        return ResourceCategory.PATCH
    if _looks_like_launcher(candidate_name):
        return ResourceCategory.INSTALLER
    if _looks_like_installer(candidate_name, ext, content_type):
        return ResourceCategory.INSTALLER
    if _looks_like_archive(ext, content_type):
        return ResourceCategory.ARCHIVE
    if _looks_like_documentation(candidate_name, content_type):
        return ResourceCategory.DOCUMENTATION
    return ResourceCategory.UNKNOWN


def categorize_resources(views: list[ResourceView]) -> list[ResourceCategory]:
    """Categorize a list of resources; preserves input order."""
    return [categorize_resource(v) for v in views]
