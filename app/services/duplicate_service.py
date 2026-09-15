"""V2.0 Phase 2 — Duplicate detection for the download workflow.

Reuses the existing V1.14 duplicate intelligence where possible.
Does not modify the download engine's existing file-safety behavior.
"""

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from app.core.models import DownloadFile
from app.services.resource_intelligence import FileCategory


class DuplicateState(str, Enum):
    """User-facing duplicate states for the download workflow.

    Subclasses ``str`` so existing comparisons and serialization
    remain backward-compatible.
    """
    READY = "ready"
    ALREADY_EXISTS = "already_exists"
    DUPLICATE_RESOURCE = "duplicate_resource"
    SAME_FILENAME_SIZE = "same_filename_size"
    UNKNOWN = "unknown"


@dataclass
class DuplicateCheck:
    """Result of a duplicate check before starting a download."""
    state: DuplicateState
    detail: str
    existing_path: Optional[str] = None
    existing_task_id: Optional[str] = None


def check_existing_destination(filename: str, destination: str | Path) -> DuplicateCheck:
    """Check whether the target destination file already exists.

    Does NOT delete or overwrite the existing file.
    """
    safe_name = filename
    dest_path = Path(destination)
    candidate = dest_path / safe_name

    if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
        return DuplicateCheck(
            state=DuplicateState.ALREADY_EXISTS,
            detail=f"File already exists at {candidate}",
            existing_path=str(candidate),
        )
    return DuplicateCheck(
        state=DuplicateState.READY,
        detail="Ready to download",
    )


def check_duplicate_resource(
    file: DownloadFile,
    seen_urls: dict[str, str],
    seen_filename_size: dict[tuple[str, int], str],
    existing_task_ids: set[str] | None = None,
) -> DuplicateCheck:
    """Check whether a resource is a duplicate of one already selected.

    Uses the same normalized-URL and filename+size logic as
    ResourceIntelligence._detect_duplicate, but returns a UI-friendly
    result.  Does not mutate the download engine.
    """
    from urllib.parse import urlparse

    url = file.url
    if not url:
        return DuplicateCheck(state=DuplicateState.READY, detail="Ready to download")

    # Normalize URL the same way ResourceIntelligence does.
    parts = urlparse(url.strip())
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not host or scheme not in ("http", "https"):
        return DuplicateCheck(state=DuplicateState.READY, detail="Ready to download")

    try:
        port = parts.port
    except ValueError:
        return DuplicateCheck(state=DuplicateState.READY, detail="Ready to download")

    if port is not None:
        if port < 0 or port > 65535:
            return DuplicateCheck(state=DuplicateState.READY, detail="Ready to download")
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
    try:
        from urllib.parse import urlunsplit
        normalized = urlunsplit((scheme, netloc, path, query, ""))
    except ValueError:
        return DuplicateCheck(state=DuplicateState.READY, detail="Ready to download")

    if normalized in seen_urls:
        return DuplicateCheck(
            state=DuplicateState.DUPLICATE_RESOURCE,
            detail=f"Duplicate URL already selected: {seen_urls[normalized]}",
            existing_task_id=seen_urls[normalized],
        )

    if file.size is not None:
        key = (file.name.lower(), file.size)
        if key in seen_filename_size:
            return DuplicateCheck(
                state=DuplicateState.SAME_FILENAME_SIZE,
                detail=f"Same filename + size already selected: {seen_filename_size[key]}",
                existing_task_id=seen_filename_size[key],
            )

    return DuplicateCheck(state=DuplicateState.READY, detail="Ready to download")


def duplicate_state_label(state: str) -> str:
    """Return a user-facing label for a duplicate state."""
    labels = {
        DuplicateState.READY: "Ready to download",
        DuplicateState.ALREADY_EXISTS: "Already exists",
        DuplicateState.DUPLICATE_RESOURCE: "Duplicate resource",
        DuplicateState.SAME_FILENAME_SIZE: "Same filename + size",
        DuplicateState.UNKNOWN: "Unknown",
    }
    return labels.get(state, state)


__all__ = [
    "DuplicateState",
    "DuplicateCheck",
    "check_existing_destination",
    "check_duplicate_resource",
    "duplicate_state_label",
]