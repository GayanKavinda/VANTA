"""V1.14 — Resource Intelligence

Enriches ResourceProbeResult with:
- Filename intelligence (Content-Disposition, URL path, fallback)
- Size intelligence (Content-Length, Content-Range, probe)
- MIME intelligence (category mapping)
- Duplicate detection (normalized URL, filename + size)
- Source quality assessment (DIRECT_FILE, HIGH/MEDIUM/LOW confidence)
"""
import os
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote, urlparse

from app.sources.http_headers import (
    extract_filename_from_content_disposition,
    extract_filename_from_url,
    normalize_media_type,
    safe_content_length,
)


class FileCategory:
    ARCHIVE = "archive"
    INSTALLER = "installer"
    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"
    BINARY = "binary"
    UNKNOWN = "unknown"


class SourceQuality:
    DIRECT_FILE = "direct_file"
    HIGH_CONFIDENCE = "high_confidence"
    MEDIUM_CONFIDENCE = "medium_confidence"
    LOW_CONFIDENCE = "low_confidence"
    REJECTED = "rejected"


_MIME_CATEGORY_MAP = {
    "application/zip": FileCategory.ARCHIVE,
    "application/x-zip-compressed": FileCategory.ARCHIVE,
    "application/x-rar-compressed": FileCategory.ARCHIVE,
    "application/x-7z-compressed": FileCategory.ARCHIVE,
    "application/x-tar": FileCategory.ARCHIVE,
    "application/gzip": FileCategory.ARCHIVE,
    "application/x-bzip2": FileCategory.ARCHIVE,
    "application/x-xz": FileCategory.ARCHIVE,
    "application/x-msdownload": FileCategory.INSTALLER,
    "application/x-msi": FileCategory.INSTALLER,
    "application/vnd.debian.binary-package": FileCategory.INSTALLER,
    "application/x-rpm": FileCategory.INSTALLER,
    "application/x-apple-diskimage": FileCategory.INSTALLER,
    "application/vnd.android.package-archive": FileCategory.INSTALLER,
    "application/pdf": FileCategory.DOCUMENT,
    "application/msword": FileCategory.DOCUMENT,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": FileCategory.DOCUMENT,
    "application/vnd.ms-excel": FileCategory.DOCUMENT,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": FileCategory.DOCUMENT,
    "application/vnd.ms-powerpoint": FileCategory.DOCUMENT,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": FileCategory.DOCUMENT,
    "text/plain": FileCategory.TEXT,
    "text/csv": FileCategory.TEXT,
    "application/json": FileCategory.TEXT,
    "application/xml": FileCategory.TEXT,
    "text/xml": FileCategory.TEXT,
    "image/jpeg": FileCategory.IMAGE,
    "image/png": FileCategory.IMAGE,
    "image/gif": FileCategory.IMAGE,
    "image/webp": FileCategory.IMAGE,
    "image/bmp": FileCategory.IMAGE,
    "image/svg+xml": FileCategory.IMAGE,
    "video/mp4": FileCategory.VIDEO,
    "video/x-matroska": FileCategory.VIDEO,
    "video/avi": FileCategory.VIDEO,
    "video/quicktime": FileCategory.VIDEO,
    "video/webm": FileCategory.VIDEO,
    "audio/mpeg": FileCategory.AUDIO,
    "audio/flac": FileCategory.AUDIO,
    "audio/wav": FileCategory.AUDIO,
    "audio/ogg": FileCategory.AUDIO,
    "audio/mp4": FileCategory.AUDIO,
}

_EXTENSION_CATEGORY_MAP = {
    ".zip": FileCategory.ARCHIVE,
    ".rar": FileCategory.ARCHIVE,
    ".7z": FileCategory.ARCHIVE,
    ".tar": FileCategory.ARCHIVE,
    ".gz": FileCategory.ARCHIVE,
    ".tgz": FileCategory.ARCHIVE,
    ".bz2": FileCategory.ARCHIVE,
    ".xz": FileCategory.ARCHIVE,
    ".iso": FileCategory.ARCHIVE,
    ".img": FileCategory.ARCHIVE,
    ".exe": FileCategory.INSTALLER,
    ".msi": FileCategory.INSTALLER,
    ".deb": FileCategory.INSTALLER,
    ".rpm": FileCategory.INSTALLER,
    ".dmg": FileCategory.INSTALLER,
    ".apk": FileCategory.INSTALLER,
    ".pkg": FileCategory.INSTALLER,
    ".pdf": FileCategory.DOCUMENT,
    ".doc": FileCategory.DOCUMENT,
    ".docx": FileCategory.DOCUMENT,
    ".xls": FileCategory.DOCUMENT,
    ".xlsx": FileCategory.DOCUMENT,
    ".ppt": FileCategory.DOCUMENT,
    ".pptx": FileCategory.DOCUMENT,
    ".txt": FileCategory.TEXT,
    ".csv": FileCategory.TEXT,
    ".json": FileCategory.TEXT,
    ".xml": FileCategory.TEXT,
    ".jpg": FileCategory.IMAGE,
    ".jpeg": FileCategory.IMAGE,
    ".png": FileCategory.IMAGE,
    ".gif": FileCategory.IMAGE,
    ".webp": FileCategory.IMAGE,
    ".bmp": FileCategory.IMAGE,
    ".svg": FileCategory.IMAGE,
    ".mp4": FileCategory.VIDEO,
    ".mkv": FileCategory.VIDEO,
    ".avi": FileCategory.VIDEO,
    ".mov": FileCategory.VIDEO,
    ".webm": FileCategory.VIDEO,
    ".flv": FileCategory.VIDEO,
    ".mp3": FileCategory.AUDIO,
    ".flac": FileCategory.AUDIO,
    ".wav": FileCategory.AUDIO,
    ".ogg": FileCategory.AUDIO,
    ".m4a": FileCategory.AUDIO,
}


@dataclass
class FilenameIntelligence:
    filename: Optional[str]
    source: str
    confidence: str


@dataclass
class SizeIntelligence:
    size: Optional[int]
    source: str
    is_exact: bool


@dataclass
class MimeIntelligence:
    category: str
    media_type: Optional[str]
    source: str


@dataclass
class DuplicateDetection:
    is_duplicate: bool
    canonical_url: Optional[str]
    duplicate_of: Optional[str]
    reason: str


@dataclass
class ResourceIntelligenceResult:
    filename: FilenameIntelligence
    size: SizeIntelligence
    mime: MimeIntelligence
    quality: str
    duplicate: DuplicateDetection
    reasons: list[str] = field(default_factory=list)


class ResourceIntelligence:
    """Enriches raw probe results with intelligence layers."""

    def __init__(
        self,
        allow_private_networks: bool = False,
        blocked_hosts: tuple[str, ...] = (),
    ):
        self._allow_private = allow_private_networks
        self._blocked_hosts = blocked_hosts
        self._seen_resources: dict[str, str] = {}
        self._seen_filename_size: dict[tuple[str, int], str] = {}

    def enrich(self, probe_result) -> ResourceIntelligenceResult:
        """Enrich a ResourceProbeResult with all intelligence layers."""
        reasons = []

        filename_intel = self._extract_filename(probe_result, reasons)
        size_intel = self._extract_size(probe_result, reasons)
        mime_intel = self._extract_mime_category(probe_result, reasons)
        duplicate_intel = self._detect_duplicate(probe_result, filename_intel, size_intel, reasons)
        quality = self._assess_quality(probe_result, filename_intel, size_intel, mime_intel, duplicate_intel, reasons)

        return ResourceIntelligenceResult(
            filename=filename_intel,
            size=size_intel,
            mime=mime_intel,
            quality=quality,
            duplicate=duplicate_intel,
            reasons=reasons,
        )

    def _extract_filename(self, probe_result, reasons: list[str]) -> FilenameIntelligence:
        """Extract best filename from multiple sources."""
        content_disposition = getattr(probe_result, "content_disposition", None)
        final_url = getattr(probe_result, "final_url", None) or getattr(probe_result, "url", None)

        if content_disposition:
            filename = extract_filename_from_content_disposition(content_disposition)
            if filename:
                reasons.append("filename from Content-Disposition")
                return FilenameIntelligence(filename=filename, source="content_disposition", confidence="high")

        if final_url:
            filename = extract_filename_from_url(final_url)
            if filename:
                reasons.append("filename from URL path")
                return FilenameIntelligence(filename=filename, source="url_path", confidence="medium")

        if final_url:
            parsed = urlparse(final_url)
            host = parsed.hostname or "download"
            filename = f"{host}.bin"
            reasons.append("generated fallback filename")
            return FilenameIntelligence(filename=filename, source="generated", confidence="low")

        return FilenameIntelligence(filename=None, source="none", confidence="none")

    def _extract_size(self, probe_result, reasons: list[str]) -> SizeIntelligence:
        """Extract size from Content-Length, Content-Range, or probe."""
        content_length = getattr(probe_result, "content_length", None)
        content_range = getattr(probe_result, "content_range", None)
        size = getattr(probe_result, "size", None)

        if content_length is not None:
            size_val = safe_content_length(str(content_length))
            if size_val is not None:
                reasons.append("size from Content-Length")
                return SizeIntelligence(size=size_val, source="content_length", is_exact=True)

        if content_range:
            try:
                parts = content_range.split("/")
                if len(parts) == 2:
                    total = int(parts[1])
                    reasons.append("size from Content-Range")
                    return SizeIntelligence(size=total, source="content_range", is_exact=True)
            except (ValueError, IndexError):
                pass

        if size is not None:
            reasons.append("size from probe")
            return SizeIntelligence(size=size, source="probe", is_exact=True)

        return SizeIntelligence(size=None, source="none", is_exact=False)

    def _extract_mime_category(self, probe_result, reasons: list[str]) -> MimeIntelligence:
        """Map media type to file category."""
        content_type = getattr(probe_result, "content_type", None)
        final_url = getattr(probe_result, "final_url", None) or getattr(probe_result, "url", None)

        media_type = normalize_media_type(content_type)

        if media_type and media_type in _MIME_CATEGORY_MAP:
            category = _MIME_CATEGORY_MAP[media_type]
            reasons.append(f"MIME category: {category} ({media_type})")
            return MimeIntelligence(category=category, media_type=media_type, source="mime_type")

        if final_url:
            ext = os.path.splitext(urlparse(final_url).path)[1].lower()
            if ext in _EXTENSION_CATEGORY_MAP:
                category = _EXTENSION_CATEGORY_MAP[ext]
                reasons.append(f"extension category: {category} ({ext})")
                return MimeIntelligence(category=category, media_type=media_type, source="extension")

        return MimeIntelligence(category=FileCategory.UNKNOWN, media_type=media_type, source="none")

    def _detect_duplicate(
        self,
        probe_result,
        filename_intel: FilenameIntelligence,
        size_intel: SizeIntelligence,
        reasons: list[str],
    ) -> DuplicateDetection:
        """Detect duplicate resources by normalized URL or filename+size."""
        url = getattr(probe_result, "url", None) or getattr(probe_result, "final_url", None)
        final_url = getattr(probe_result, "final_url", None) or url

        if not url:
            return DuplicateDetection(False, None, None, "no URL")

        normalized_url = self._normalize_url(url)
        if normalized_url in self._seen_resources:
            original = self._seen_resources[normalized_url]
            reasons.append(f"duplicate URL: {original}")
            return DuplicateDetection(True, original, normalized_url, "normalized URL match")

        self._seen_resources[normalized_url] = normalized_url

        if filename_intel.filename and size_intel.size is not None:
            key = (filename_intel.filename.lower(), size_intel.size)
            if key in self._seen_filename_size:
                original = self._seen_filename_size[key]
                reasons.append(f"duplicate filename+size: {original}")
                return DuplicateDetection(True, original, normalized_url, "filename+size match")
            self._seen_filename_size[key] = normalized_url

        return DuplicateDetection(False, None, None, "unique")

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        if not url:
            return url
        parts = urlparse(url.strip())
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            return url
        host = (parts.hostname or "").lower()
        if not host:
            return url
        try:
            port = parts.port
        except ValueError:
            return url
        if port is not None:
            if port < 0 or port > 65535:
                return url
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
            return urlunsplit((scheme, netloc, path, query, ""))
        except ValueError:
            return url

    def _assess_quality(
        self,
        probe_result,
        filename_intel: FilenameIntelligence,
        size_intel: SizeIntelligence,
        mime_intel: MimeIntelligence,
        duplicate_intel: DuplicateDetection,
        reasons: list[str],
    ) -> str:
        """Assess overall source quality."""
        if duplicate_intel.is_duplicate:
            reasons.append("quality: rejected (duplicate)")
            return SourceQuality.REJECTED

        is_downloadable = getattr(probe_result, "is_downloadable", False)
        status_code = getattr(probe_result, "status_code", 0)

        if status_code >= 400:
            reasons.append("quality: rejected (HTTP error)")
            return SourceQuality.REJECTED

        if not is_downloadable:
            reasons.append("quality: rejected (not downloadable)")
            return SourceQuality.REJECTED

        if filename_intel.source == "content_disposition" and size_intel.source == "content_length":
            reasons.append("quality: direct_file")
            return SourceQuality.DIRECT_FILE

        if filename_intel.confidence == "high" and size_intel.is_exact:
            reasons.append("quality: high_confidence")
            return SourceQuality.HIGH_CONFIDENCE

        if filename_intel.confidence == "medium" or size_intel.is_exact:
            reasons.append("quality: medium_confidence")
            return SourceQuality.MEDIUM_CONFIDENCE

        reasons.append("quality: low_confidence")
        return SourceQuality.LOW_CONFIDENCE

    def reset(self):
        """Reset deduplication state."""
        self._seen_resources.clear()
        self._seen_filename_size.clear()