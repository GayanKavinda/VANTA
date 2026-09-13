"""V1.10 tests — error classification utility."""
from __future__ import annotations

from app.core.queue_controller import classify_download_error
from app.core.task_manager import DownloadErrorType, DownloadTask, TaskStatus


def _task(error: str | None = None, error_type=DownloadErrorType.UNKNOWN) -> DownloadTask:
    return DownloadTask(
        id="t1",
        name="example.zip",
        source_url="https://example.com/page",
        download_url="https://example.com/example.zip",
        destination="/tmp/example.zip",
        error=error,
        error_type=error_type,
    )


def test_classify_html_response():
    task = _task(error="Server returned an HTML page instead of 'example.zip' (Content-Type: text/html). This usually means access was denied or blocked.")
    result = classify_download_error(task)
    assert result is DownloadErrorType.HTML_RESPONSE


def test_classify_access_denied():
    task = _task(error="Server returned a text response instead of 'example.zip' (Content-Type: text/plain).")
    result = classify_download_error(task)
    assert result is DownloadErrorType.ACCESS_DENIED


def test_classify_unsafe_redirect():
    task = _task(error="Unsafe download URL: host 127.0.0.1 is blocked (unsafe_url)")
    result = classify_download_error(task)
    assert result is DownloadErrorType.UNSAFE_REDIRECT


def test_classify_redirect_loop():
    task = _task(error="Redirect loop detected after 5 redirects (redirect_loop)")
    result = classify_download_error(task)
    assert result is DownloadErrorType.REDIRECT_LOOP


def test_classify_range_unsupported():
    task = _task(error="Server returned 416 Range Not Satisfiable — not resumable")
    result = classify_download_error(task)
    assert result is DownloadErrorType.RANGE_UNSUPPORTED


def test_classify_verification():
    task = _task(error="File verification failed — checksum mismatch")
    result = classify_download_error(task)
    assert result is DownloadErrorType.VERIFICATION


def test_classify_disk_error():
    task = _task(error="No space left on device")
    result = classify_download_error(task)
    assert result is DownloadErrorType.DISK


def test_classify_disk_error_permission():
    task = _task(error="Permission denied: cannot write to /read-only/path")
    result = classify_download_error(task)
    assert result is DownloadErrorType.DISK


def test_classify_unknown_error_stays_unknown():
    task = _task(error="Some unexpected error occurred")
    result = classify_download_error(task)
    assert result is DownloadErrorType.UNKNOWN


def test_classify_no_error_message_stays_unknown():
    task = _task(error=None)
    result = classify_download_error(task)
    assert result is DownloadErrorType.UNKNOWN


def test_classify_preserves_already_classified():
    task = _task(
        error="Server returned an HTML page",
        error_type=DownloadErrorType.NETWORK,
    )
    result = classify_download_error(task)
    assert result is DownloadErrorType.NETWORK


def test_classify_preserves_html_response_already_set():
    task = _task(
        error="Server returned an HTML page",
        error_type=DownloadErrorType.HTML_RESPONSE,
    )
    result = classify_download_error(task)
    assert result is DownloadErrorType.HTML_RESPONSE


def test_classify_network_error_message():
    task = _task(error="Failed after 3 redirect steps (network)")
    result = classify_download_error(task)
    assert result is DownloadErrorType.UNKNOWN


def test_classify_disk_file_not_found():
    task = _task(error="[Errno 2] No such file or directory: '/tmp/missing.part'")
    result = classify_download_error(task)
    assert result is DownloadErrorType.DISK


# ── V1.13 — New failure classifications ──────────────────────────────────

def test_classify_timeout():
    task = _task(error="Read timeout after 60 seconds")
    result = classify_download_error(task)
    assert result is DownloadErrorType.TIMEOUT


def test_classify_timeout_timed_out():
    task = _task(error="Connection timed out")
    result = classify_download_error(task)
    assert result is DownloadErrorType.TIMEOUT


def test_classify_connection_interrupted():
    task = _task(error="Connection reset by peer")
    result = classify_download_error(task)
    assert result is DownloadErrorType.CONNECTION_INTERRUPTED


def test_classify_connection_aborted():
    task = _task(error="Connection aborted")
    result = classify_download_error(task)
    assert result is DownloadErrorType.CONNECTION_INTERRUPTED


def test_classify_connection_broken():
    task = _task(error="Broken pipe")
    result = classify_download_error(task)
    assert result is DownloadErrorType.CONNECTION_INTERRUPTED


def test_classify_server_unavailable_503():
    task = _task(error="503 Service Unavailable")
    result = classify_download_error(task)
    assert result is DownloadErrorType.SERVER_UNAVAILABLE


def test_classify_server_unavailable_502():
    task = _task(error="502 Bad Gateway")
    result = classify_download_error(task)
    assert result is DownloadErrorType.SERVER_UNAVAILABLE


def test_classify_server_unavailable_504():
    task = _task(error="504 Gateway Timeout")
    result = classify_download_error(task)
    assert result is DownloadErrorType.SERVER_UNAVAILABLE


def test_classify_connection_refused():
    task = _task(error="Connection refused")
    result = classify_download_error(task)
    assert result is DownloadErrorType.SERVER_UNAVAILABLE


def test_classify_existing_file():
    task = _task(error="File exists: /path/to/file.zip")
    result = classify_download_error(task)
    assert result is DownloadErrorType.EXISTING_FILE


def test_classify_already_exists():
    task = _task(error="Destination already exists")
    result = classify_download_error(task)
    assert result is DownloadErrorType.EXISTING_FILE


def test_classify_corrupt_partial():
    task = _task(error="Corrupt partial file detected")
    result = classify_download_error(task)
    assert result is DownloadErrorType.CORRUPT_PARTIAL


def test_classify_checksum_mismatch():
    task = _task(error="Checksum mismatch on partial file")
    result = classify_download_error(task)
    assert result is DownloadErrorType.CORRUPT_PARTIAL
