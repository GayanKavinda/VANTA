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
    confidence: str = "low"
    confidence_label: str = "LOW"
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    size_label: str = ""
    type_label: str = ""


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


def build_resource_view(
    file: DownloadFile,
    resolution: Optional[ResolvedResource],
) -> ResourceView:
    confidence = "low"
    score = 0
    reasons: list[str] = []

    if resolution is not None:
        confidence = resolution.confidence or "low"
        score = resolution.score
        reasons = list(resolution.reasons)

    return ResourceView(
        file=file,
        confidence=confidence,
        confidence_label=CONFIDENCE_LABELS.get(confidence, "LOW"),
        score=score,
        reasons=reasons,
        size_label=format_size(file.size),
        type_label=format_content_type(file.content_type),
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