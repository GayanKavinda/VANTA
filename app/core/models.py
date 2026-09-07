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
