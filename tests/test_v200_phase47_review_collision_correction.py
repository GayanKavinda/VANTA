"""V2.0 Phase 4.7 - review-boundary collision corrections."""
from __future__ import annotations

from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.services.analysis_view import ResourceView
from app.services.download_workflow import DownloadWorkflowService
from app.ui.bulk_review import BulkReviewDialog
from app.ui.download_review import DownloadReviewDialog


def _file(name: str) -> DownloadFile:
    return DownloadFile(
        name=name,
        url=f"https://example.com/{name}",
        size=1024,
        content_type="application/zip",
    )


def _single(tmp_path, policy="auto_rename"):
    destination = tmp_path / "downloads"
    destination.mkdir()
    file = _file("movie.zip")
    manager = FileManager(destination)
    workflow = DownloadWorkflowService(manager)
    duplicate = workflow.check_duplicate(file, file.name, destination)
    dialog = DownloadReviewDialog(
        file=file,
        file_manager=manager,
        download_dir=destination,
        duplicate_check=duplicate,
        workflow=workflow,
        conflict_policy=policy,
    )
    return dialog, destination


def _bulk(tmp_path, names):
    destination = tmp_path / "downloads"
    destination.mkdir()
    manager = FileManager(destination)
    views = [ResourceView(file=_file(name), confidence="high", score=80) for name in names]
    dialog = BulkReviewDialog(
        source_url="https://example.com/page",
        resource_views=views,
        file_manager=manager,
        download_dir=destination,
        workflow=DownloadWorkflowService(manager),
    )
    return dialog, destination


def test_single_auto_rename_returns_directory_and_filename(qapp, tmp_path):
    dialog, destination = _single(tmp_path)
    (destination / "movie.zip").write_bytes(b"existing")
    dialog._duplicate_check = dialog._workflow.check_duplicate(
        dialog.file, dialog.filename, destination
    )
    dialog._refresh_duplicate_label()
    dialog._on_download()
    assert dialog.destination == destination
    assert dialog.filename == "movie_1.zip"


def test_single_rename_preserves_extension_and_skips_collisions(qapp, tmp_path):
    dialog, destination = _single(tmp_path, policy="rename")
    for name in ("movie.zip", "movie_1.zip", "movie_2.zip"):
        (destination / name).write_bytes(b"existing")
    dialog._duplicate_check = dialog._workflow.check_duplicate(
        dialog.file, dialog.filename, destination
    )
    dialog._refresh_duplicate_label()
    dialog._on_download()
    assert dialog.destination == destination
    assert dialog.filename == "movie_3.zip"
    assert dialog.filename.endswith(".zip")


def test_single_non_conflicting_download_is_unchanged(qapp, tmp_path):
    dialog, destination = _single(tmp_path)
    dialog._on_download()
    assert dialog.destination == destination
    assert dialog.filename == "movie.zip"


def test_bulk_existing_numbered_files_and_intra_batch_paths_are_unique(qapp, tmp_path):
    dialog, destination = _bulk(tmp_path, ["movie.zip", "movie.zip", "movie.zip"])
    for name in ("movie.zip", "movie_1.zip", "movie_2.zip"):
        (destination / name).write_bytes(b"existing")
    dialog._recalc_all()
    dialog._on_download()

    accepted = dialog.accepted_entries
    names = [filename for _entry, filename, directory in accepted]
    paths = [directory / filename for _entry, filename, directory in accepted]
    assert names == ["movie_3.zip", "movie_4.zip", "movie_5.zip"]
    assert len(paths) == len(set(paths)) == 3
    assert all(directory == destination for _entry, _filename, directory in accepted)
    assert all(path.suffix == ".zip" for path in paths)
    assert not any(path.exists() for path in paths)


def test_bulk_accepted_tuple_keeps_filename_separate_from_directory(qapp, tmp_path):
    dialog, destination = _bulk(tmp_path, ["movie.zip"])
    (destination / "movie.zip").write_bytes(b"existing")
    dialog._recalc_all()
    dialog._on_download()
    _entry, filename, directory = dialog.accepted_entries[0]
    assert filename == "movie_1.zip"
    assert directory == destination
