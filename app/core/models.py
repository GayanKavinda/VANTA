from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DownloadFile:
    name: str
    url: str
    size: Optional[int] = None
    content_type: Optional[str] = None


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
