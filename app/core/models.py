from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DownloadFile:
    name: str
    url: str
    size: Optional[int] = None
    content_type: Optional[str] = None


class ConfidenceLevel:
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    REJECTED = "rejected"


@dataclass
class ResourceProbeResult:
    url: str
    final_url: str
    status_code: int
    content_type: Optional[str] = None
    size: Optional[int] = None
    filename: Optional[str] = None
    supports_range: Optional[bool] = None
    is_downloadable: bool = False

    # V1.14 — Additional headers for intelligence
    content_disposition: Optional[str] = None
    content_length: Optional[str] = None
    content_range: Optional[str] = None

    # V2.0 Phase 3.6 — Intelligence summary populated by ResourceIntelligence.enrich()
    filename_source: str = ""
    mime_source: str = ""
    mime_media_type: Optional[str] = None
    mime_category: str = ""
    quality: str = ""
    duplicate_is_duplicate: bool = False
    duplicate_reason: str = ""
    size_source: str = ""
    intelligence_reasons: list[str] = field(default_factory=list)


@dataclass
class ResolvedResource:
    source_url: str
    final_url: str
    filename: Optional[str]
    size: Optional[int]
    content_type: Optional[str]
    supports_range: Optional[bool]
    score: int
    confidence: str
    reasons: list[str] = field(default_factory=list)

    # V2.0 Phase 3.6 — Intelligence fields propagated from ResourceIntelligence
    filename_source: str = ""
    mime_source: str = ""
    mime_media_type: Optional[str] = None
    mime_category: str = ""
    quality: str = ""
    duplicate_is_duplicate: bool = False
    duplicate_reason: str = ""
    size_source: str = ""
    intelligence_reasons: list[str] = field(default_factory=list)

    # V2.0 Phase 3.6 — Provenance fields populated by GenericSourceAdapter
    element_type: str = ""
    discovery_attribute: str = ""
    html_type_hint: str = ""
    discovery_paths: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    title: str
    source: str
    files: list[DownloadFile] = field(default_factory=list)
    status: str = "ready"


@dataclass
class DownloadError:
    message: str
    technical_detail: str = ""
    is_recoverable: bool = True
