from app.core.models import DownloadFile, AnalysisResult, DownloadError
from app.core.task_manager import DownloadTask, TaskStatus


def test_download_file_defaults():
    f = DownloadFile(name="test.zip", url="https://example.com/test.zip")
    assert f.name == "test.zip"
    assert f.size is None
    assert f.content_type is None


def test_download_file_with_content_type():
    f = DownloadFile(
        name="test.zip",
        url="https://example.com/test.zip",
        size=1024,
        content_type="application/zip",
    )
    assert f.content_type == "application/zip"
    assert f.size == 1024


def test_analysis_result_defaults():
    result = AnalysisResult(title="Test", source="Direct Download")
    assert result.files == []
    assert result.status == "ready"


def test_download_error_fields():
    err = DownloadError(message="Connection failed", technical_detail="timeout", is_recoverable=True)
    assert err.message == "Connection failed"
    assert err.is_recoverable is True


def test_task_status_values():
    for status in TaskStatus:
        assert status.value is not None
        assert status.value != ""


def test_download_task_progress():
    task = DownloadTask(
        id="12345678",
        name="test.zip",
        source_url="https://example.com",
        download_url="https://example.com/file.zip",
        destination="/tmp/test.zip",
        total_size=1000,
    )
    task.update_progress(500, 1000, 1.5)
    assert task.progress == 50.0
    assert task.downloaded_size == 500
    assert task.speed == 1.5


def test_download_task_is_active():
    task = DownloadTask(id="1", name="test", source_url="", download_url="", destination="/tmp")
    task.status = TaskStatus.DOWNLOADING
    assert task.is_active is True
    task.status = TaskStatus.COMPLETED
    assert task.is_terminal is True
    assert task.is_active is False


def test_task_to_dict():
    task = DownloadTask(
        id="abcd1234",
        name="file.zip",
        source_url="https://example.com",
        download_url="https://example.com/file.zip",
        destination="/tmp",
        total_size=2048,
    )
    d = task.to_dict()
    assert d["id"] == "abcd1234"
    assert d["name"] == "file.zip"
    assert d["total_size"] == 2048
    assert d["status"] == "queued"
