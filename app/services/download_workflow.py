"""V2.0 Phase 2 — Download workflow orchestration.

Connects the review dialog to the real download flow without modifying
any frozen V1.x architecture.

Responsibilities:
- Determine the proposed sanitized filename.
- Determine the selected/default destination.
- Check whether that exact destination + filename already exists.
- Check duplicate resource information where available.
- Recalculate duplicate state when filename/destination changes.
- Validate the final destination + filename combination.
"""

from pathlib import Path
from typing import Optional

from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.task_manager import DownloadTask
from app.services.duplicate_service import (
    DuplicateCheck,
    DuplicateState,
    check_duplicate_resource,
    check_existing_destination,
)
from app.services.filename_service import (
    is_safe_within_directory,
    sanitize_filename,
)
from app.services.resource_intelligence import FileCategory, _EXTENSION_CATEGORY_MAP
from app.utils.logger import get_logger

log = get_logger("vanta.services.download_workflow")


class DownloadWorkflowService:
    """Orchestrates duplicate detection and validation for the download flow.

    Does not touch the download engine.  Works with the existing
    FileManager, QueueController, and DownloadService public APIs.
    """

    def __init__(self, file_manager: FileManager):
        self._file_manager = file_manager
        # Tracks resources already selected in this session for duplicate detection.
        self._seen_urls: dict[str, str] = {}
        self._seen_filename_size: dict[tuple[str, int], str] = {}

    # ── Duplicate detection ─────────────────────────────────────────────

    def check_duplicate(
        self,
        file: DownloadFile,
        filename: str,
        destination: str | Path,
    ) -> DuplicateCheck:
        """Run all duplicate checks for a proposed download.

        Returns the most severe duplicate state found.
        """
        # Check existing destination first (highest priority for UX).
        dest_check = check_existing_destination(filename, destination)
        if dest_check.state == DuplicateState.ALREADY_EXISTS:
            return dest_check

        # Check duplicate resource (URL or filename+size already selected).
        resource_check = check_duplicate_resource(
            file, self._seen_urls, self._seen_filename_size
        )
        if resource_check.state != DuplicateState.READY:
            return resource_check

        return DuplicateCheck(
            state=DuplicateState.READY,
            detail="Ready to download",
        )

    def register_selected(self, task: DownloadTask):
        """Record a task as selected so future duplicate checks see it."""
        if not task.download_url:
            return
        self._register_url(task.download_url, task.id)
        if task.name and task.total_size:
            self._seen_filename_size[(task.name.lower(), task.total_size)] = task.id

    def _register_url(self, url: str, task_id: str):
        from urllib.parse import urlparse, urlunsplit

        try:
            parts = urlparse(url.strip())
            scheme = (parts.scheme or "").lower()
            host = (parts.hostname or "").lower()
            if not host or scheme not in ("http", "https"):
                return
            try:
                port = parts.port
            except ValueError:
                return
            if port is not None:
                if port < 0 or port > 65535:
                    return
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
            self._seen_urls[normalized] = task_id
        except Exception:
            pass

    # ── Validation ──────────────────────────────────────────────────────

    def validate_final(
        self,
        filename: str,
        destination: str | Path,
    ) -> tuple[bool, str]:
        """Validate the final filename + destination before queueing.

        Returns (is_valid, error_message).
        """
        safe_name = sanitize_filename(filename)
        if not safe_name:
            return False, "Filename is empty or invalid"

        dest = Path(destination)
        if not dest.exists():
            return False, f"Destination does not exist: {dest}"
        if not dest.is_dir():
            return False, f"Destination is not a directory: {dest}"

        if not is_safe_within_directory(safe_name, dest):
            return False, f"Filename escapes destination directory: {safe_name}"

        return True, ""

    # ── Category ────────────────────────────────────────────────────────

    @staticmethod
    def file_category(file: DownloadFile, content_type: str | None = None) -> str:
        """Derive the file category from MIME type or filename extension.

        Reuses the existing ResourceIntelligence extension map.
        Does NOT invent a second classification system.
        """
        media_type = (content_type or file.content_type or "").split(";", 1)[0].strip().lower()
        from app.services.resource_intelligence import _MIME_CATEGORY_MAP
        if media_type and media_type in _MIME_CATEGORY_MAP:
            return _MIME_CATEGORY_MAP[media_type]

        ext = Path(file.name).suffix.lower()
        if ext in _EXTENSION_CATEGORY_MAP:
            return _EXTENSION_CATEGORY_MAP[ext]

        return FileCategory.UNKNOWN

    @staticmethod
    def format_category_label(category: str) -> str:
        from app.services.filename_service import file_category_label
        return file_category_label(category)


__all__ = ["DownloadWorkflowService"]