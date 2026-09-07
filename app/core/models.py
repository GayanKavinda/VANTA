from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DownloadFile:
    name: str
    url: str
    size: Optional[int] = None
    content_type: Optional[str] = None


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
