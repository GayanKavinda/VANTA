"""V1.15 — Structured Diagnostics & Logging

Provides rich diagnostic information for failures:
- Task execution context
- Failure classification with full context
- HTTP transaction details
- Retry history
"""
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus


class FailurePhase(str, Enum):
    """Phase in which failure occurred."""
    PREPARING = "preparing"
    REDIRECT_WALK = "redirect_walk"
    RESPONSE_VALIDATION = "response_validation"
    STREAMING = "streaming"
    VERIFICATION = "verification"
    FILE_WRITE = "file_write"
    UNKNOWN = "unknown"


@dataclass
class DiagnosticContext:
    """Complete diagnostic context for a download failure."""
    task_id: str
    task_name: str
    source_url: str
    download_url: str
    destination: str
    phase: FailurePhase
    error_type: DownloadErrorType
    error_message: str
    http_status: Optional[int] = None
    http_response_headers: dict[str, str] = field(default_factory=dict)
    redirect_chain: list[str] = field(default_factory=list)
    final_url: Optional[str] = None
    downloaded_size: int = 0
    total_size: int = 0
    speed: float = 0.0
    retry_count: int = 0
    supports_resume: bool = False
    resume_position: int = 0
    part_file_size: Optional[int] = None
    duration_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)
    exception_type: Optional[str] = None
    exception_traceback: Optional[str] = None


@dataclass
class RetryDiagnostic:
    """Diagnostic info for a retry attempt."""
    attempt: int
    timestamp: float
    error_type: DownloadErrorType
    error_message: str
    phase: FailurePhase
    downloaded_before_retry: int
    was_resumed: bool


def build_diagnostic_context(
    task: DownloadTask,
    phase: FailurePhase,
    error_type: DownloadErrorType,
    error_message: str,
    **kwargs
) -> DiagnosticContext:
    """Build diagnostic context from a failed task."""
    part_file_size = None
    if task.destination:
        from pathlib import Path
        part_path = Path(task.destination).with_suffix(Path(task.destination).suffix + ".part")
        if part_path.exists():
            part_file_size = part_path.stat().st_size

    return DiagnosticContext(
        task_id=task.id,
        task_name=task.name,
        source_url=task.source_url,
        download_url=task.download_url,
        destination=task.destination,
        phase=phase,
        error_type=error_type,
        error_message=error_message,
        http_status=kwargs.get("http_status"),
        http_response_headers=kwargs.get("http_response_headers", {}),
        redirect_chain=kwargs.get("redirect_chain", []),
        final_url=kwargs.get("final_url"),
        downloaded_size=task.downloaded_size,
        total_size=task.total_size,
        speed=task.speed,
        retry_count=kwargs.get("retry_count", 0),
        supports_resume=task.supports_resume,
        resume_position=kwargs.get("resume_position", 0),
        part_file_size=part_file_size,
        duration_seconds=kwargs.get("duration_seconds", 0.0),
        timestamp=time.time(),
        exception_type=kwargs.get("exception_type"),
        exception_traceback=kwargs.get("exception_traceback"),
    )


def format_diagnostic(context: DiagnosticContext) -> str:
    """Format diagnostic context for logging."""
    lines = [
        "=== DOWNLOAD FAILURE DIAGNOSTIC ===",
        f"Task ID:       {context.task_id}",
        f"Task Name:     {context.task_name}",
        f"Source URL:    {context.source_url}",
        f"Download URL:  {context.download_url}",
        f"Destination:   {context.destination}",
        f"Phase:         {context.phase.value}",
        f"Error Type:    {context.error_type.value}",
        f"Error Message: {context.error_message}",
        f"HTTP Status:   {context.http_status or 'N/A'}",
        f"Final URL:     {context.final_url or 'N/A'}",
        f"Redirects:     {len(context.redirect_chain)}",
        f"Downloaded:    {context.downloaded_size} / {context.total_size or '?'} bytes",
        f"Speed:         {context.speed:.1f} B/s",
        f"Retries:       {context.retry_count}",
        f"Resume:        {context.supports_resume} (pos: {context.resume_position})",
        f"Part File:     {context.part_file_size or 'N/A'} bytes",
        f"Duration:      {context.duration_seconds:.2f}s",
        f"Timestamp:     {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(context.timestamp))}",
    ]
    if context.exception_type:
        lines.append(f"Exception:     {context.exception_type}")
    if context.exception_traceback:
        lines.append(f"Traceback:\n{context.exception_traceback}")
    if context.http_response_headers:
        lines.append("Response Headers:")
        for k, v in context.http_response_headers.items():
            lines.append(f"  {k}: {v}")
    if context.redirect_chain:
        lines.append("Redirect Chain:")
        for i, url in enumerate(context.redirect_chain):
            lines.append(f"  {i+1}. {url}")
    return "\n".join(lines)


def log_diagnostic(context: DiagnosticContext):
    """Log diagnostic context at ERROR level."""
    from app.utils.logger import get_logger
    log = get_logger("vanta.diagnostics")
    log.error(format_diagnostic(context))