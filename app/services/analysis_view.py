"""View-model builder for analysis results.

Transforms `AnalysisResult` + `ResolutionContext` into a UI-friendly
structure with confidence labels, human-readable size, and reason chips.
Pure data transformation — no Qt, no I/O.
"""
from dataclasses import dataclass, field
from typing import Optional

from app.core.models import AnalysisResult, DownloadFile, ResolvedResource
from app.services.analyzer import ResolutionContext


CONFIDENCE_LABELS = {
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
    "rejected": "REJECTED",
}


@dataclass
class ResourceView:
    file: DownloadFile
    source_url: str = ""
    final_url: str = ""
    confidence: str = "low"
    confidence_label: str = "LOW"
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    size_label: str = ""
    type_label: str = ""
    # V2.0 Phase 3.6 — Intelligence and provenance fields
    filename_source: str = ""
    mime_source: str = ""
    mime_media_type: Optional[str] = None
    mime_category: str = ""
    quality: str = ""
    duplicate_is_duplicate: bool = False
    duplicate_reason: str = ""
    size_source: str = ""
    element_type: str = ""
    discovery_attribute: str = ""
    html_type_hint: str = ""
    discovery_paths: list[str] = field(default_factory=list)
    resolution_explanation: str = ""


@dataclass
class AnalysisViewModel:
    title: str
    source: str
    status: str
    resources: list[ResourceView] = field(default_factory=list)
    has_resources: bool = False

    @property
    def is_ready(self) -> bool:
        return self.status == "ready" and self.has_resources

    @property
    def is_unsupported(self) -> bool:
        return self.status == "unsupported"

    @property
    def is_error(self) -> bool:
        return self.status == "error"


def format_size(n: int | float | None) -> str:
    if n is None or n < 0:
        return ""
    num = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            if unit == "B":
                return f"{num:.0f} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def format_content_type(ct: Optional[str]) -> str:
    if not ct:
        return ""
    media = ct.split(";", 1)[0].strip()
    return media


def _build_resolution_explanation(reasons: list[str], element_type: str, discovery_attribute: str) -> str:
    """Build a human-readable explanation of why this resource is considered downloadable."""
    if not reasons:
        return "Unknown"

    # Check for direct resource indicators
    has_probe_success = any("downloadable MIME" in r or "Content-Disposition" in r or "file extension" in r for r in reasons)
    has_http_error = any("HTTP" in r and "non-success" in r for r in reasons)

    if has_http_error:
        return "HTTP error - not downloadable"

    if has_probe_success:
        return "Direct resource"

    # Check for HTML-based discovery
    if element_type in ("img", "image", "video", "audio", "source", "object", "embed", "picture"):
        if discovery_attribute == "srcset":
            return "Discovered through image srcset"
        if discovery_attribute and discovery_attribute.startswith("data-"):
            return "Discovered through lazy loading"
        return f"Discovered through {element_type} element"

    if element_type == "link":
        if discovery_attribute == "preload":
            return "Discovered through preload link"
        return "Resolved from an HTML download link"

    if element_type == "meta":
        return "Discovered through metadata"

    # Check for other reasons
    if any("navigation-style URL" in r for r in reasons):
        return "Navigation-style URL (low confidence)"

    if any("download-like path" in r for r in reasons):
        return "Resolved from a download-like path"

    if any("download-suggesting anchor text" in r for r in reasons):
        return "Resolved from anchor text suggesting download"

    return "Resolved after HTTP resource probing"


def build_resource_view(
    file: DownloadFile,
    resolution: Optional[ResolvedResource],
) -> ResourceView:
    confidence = "low"
    score = 0
    reasons: list[str] = []
    element_type = ""
    discovery_attribute = ""
    discovery_paths: list[str] = []

    if resolution is not None:
        confidence = resolution.confidence or "low"
        score = resolution.score
        reasons = list(dict.fromkeys(resolution.reasons))
        element_type = resolution.element_type or ""
        discovery_attribute = resolution.discovery_attribute or ""
        discovery_paths = list(resolution.discovery_paths) if resolution.discovery_paths else []

    resolution_explanation = _build_resolution_explanation(reasons, element_type, discovery_attribute)
    size = resolution.size if resolution and resolution.size is not None else file.size
    content_type = resolution.content_type if resolution and resolution.content_type else file.content_type

    return ResourceView(
        file=file,
        source_url=resolution.source_url if resolution else "",
        final_url=resolution.final_url if resolution else file.url,
        confidence=confidence,
        confidence_label=CONFIDENCE_LABELS.get(confidence, "LOW"),
        score=score,
        reasons=reasons,
        size_label=format_size(size),
        type_label=format_content_type(resolution.mime_media_type if resolution and resolution.mime_media_type else content_type),
        # V2.0 Phase 3.6 — Propagate intelligence and provenance from resolution
        filename_source=resolution.filename_source if resolution else "",
        mime_source=resolution.mime_source if resolution else "",
        mime_media_type=resolution.mime_media_type if resolution else None,
        mime_category=resolution.mime_category if resolution else "",
        quality=resolution.quality if resolution else "",
        duplicate_is_duplicate=resolution.duplicate_is_duplicate if resolution else False,
        duplicate_reason=resolution.duplicate_reason if resolution else "",
        size_source=resolution.size_source if resolution else "",
        element_type=element_type,
        discovery_attribute=discovery_attribute,
        html_type_hint=resolution.html_type_hint if resolution else "",
        discovery_paths=discovery_paths,
        resolution_explanation=resolution_explanation,
    )


def build_view_model(
    result: AnalysisResult,
    context: Optional[ResolutionContext] = None,
) -> AnalysisViewModel:
    context = context or ResolutionContext()
    resources: list[ResourceView] = []

    for f in result.files:
        resolution = context.for_file(f)
        resources.append(build_resource_view(f, resolution))

    return AnalysisViewModel(
        title=result.title,
        source=result.source,
        status=result.status,
        resources=resources,
        has_resources=bool(resources),
    )