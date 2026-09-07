"""V1.9.0 Phase 2 — Resource categorization tests.

Validates the categorization contract:
  * Pure Python, no I/O, no resolver/probe calls.
  * Inspects only `ResourceView.file` (name, url, content_type).
  * Precedence: PART > PATCH > INSTALLER > ARCHIVE > DOCUMENTATION > UNKNOWN.
  * Deterministic.
  * Non-mutating.
  * Safe for missing filename / content_type.
"""
from __future__ import annotations

import pytest

from app.core.models import DownloadFile
from app.services.analysis_view import ResourceView
from app.services.categorization import (
    ResourceCategory,
    categorize_resource,
    categorize_resources,
)


def _view(
    name: str,
    *,
    content_type: str | None = None,
    url: str | None = None,
) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=url or f"https://example.com/{name}",
            content_type=content_type,
        ),
        confidence="high",
        score=80,
    )


def test_installer_exe():
    assert categorize_resource(_view("setup.exe")) is ResourceCategory.INSTALLER


def test_installer_msi():
    assert categorize_resource(_view("installer.msi")) is ResourceCategory.INSTALLER


def test_installer_dmg_and_pkg():
    assert categorize_resource(_view("app.dmg")) is ResourceCategory.INSTALLER
    assert categorize_resource(_view("app.pkg")) is ResourceCategory.INSTALLER


def test_installer_via_content_type_when_extension_missing():
    v = _view("payload", content_type="application/x-msi")
    assert categorize_resource(v) is ResourceCategory.INSTALLER


def test_installer_via_android_package_mime():
    v = _view("payload", content_type="application/vnd.android.package-archive")
    assert categorize_resource(v) is ResourceCategory.INSTALLER


def test_archive_zip():
    assert categorize_resource(_view("game.zip")) is ResourceCategory.ARCHIVE


def test_archive_rar_7z_tar_gz():
    assert categorize_resource(_view("data.rar")) is ResourceCategory.ARCHIVE
    assert categorize_resource(_view("data.7z")) is ResourceCategory.ARCHIVE
    assert categorize_resource(_view("data.tar")) is ResourceCategory.ARCHIVE
    assert categorize_resource(_view("data.tar.gz")) is ResourceCategory.ARCHIVE


def test_archive_via_content_type():
    v = _view("payload", content_type="application/zip")
    assert categorize_resource(v) is ResourceCategory.ARCHIVE


def test_documentation_pdf_and_txt():
    assert categorize_resource(_view("manual.pdf")) is ResourceCategory.DOCUMENTATION
    assert categorize_resource(_view("notes.txt")) is ResourceCategory.DOCUMENTATION


def test_documentation_via_content_type_text():
    v = _view("payload", content_type="text/markdown")
    assert categorize_resource(v) is ResourceCategory.DOCUMENTATION


def test_documentation_via_readme_hint():
    v = _view("README", content_type="text/plain")
    assert categorize_resource(v) is ResourceCategory.DOCUMENTATION


def test_multipart_archives_are_parts():
    assert categorize_resource(_view("game.part1.rar")) is ResourceCategory.PART
    assert categorize_resource(_view("game.part2.rar")) is ResourceCategory.PART
    assert categorize_resource(_view("game.part01.zip")) is ResourceCategory.PART
    assert categorize_resource(_view("game.zip.001")) is ResourceCategory.PART
    assert categorize_resource(_view("game.rar.002")) is ResourceCategory.PART
    assert categorize_resource(_view("game.r00")) is ResourceCategory.PART
    assert categorize_resource(_view("game.z01")) is ResourceCategory.PART


def test_multipart_naming_variants():
    assert categorize_resource(_view("Game-Part1.zip")) is ResourceCategory.PART
    assert categorize_resource(_view("Game.Part1.zip")) is ResourceCategory.PART
    assert categorize_resource(_view("Game-Part2.zip")) is ResourceCategory.PART


def test_patch_update_naming():
    assert categorize_resource(_view("game_patch_v1.2.exe")) is ResourceCategory.PATCH
    assert categorize_resource(_view("update.exe")) is ResourceCategory.PATCH
    assert categorize_resource(_view("hotfix.zip")) is ResourceCategory.PATCH
    assert categorize_resource(_view("delta-update.exe")) is ResourceCategory.PATCH


def test_patch_keyword_inside_generic_archive():
    assert categorize_resource(_view("gamepatch_v1.zip")) is ResourceCategory.PATCH


def test_launcher_is_treated_as_installer():
    assert categorize_resource(_view("launcher.exe")) is ResourceCategory.INSTALLER
    assert categorize_resource(_view("game_boot.bin")) is ResourceCategory.INSTALLER


def test_case_insensitivity():
    assert categorize_resource(_view("SETUP.EXE")) is ResourceCategory.INSTALLER
    assert categorize_resource(_view("GAME.ZIP")) is ResourceCategory.ARCHIVE
    assert categorize_resource(_view("Game-PART1.ZIP")) is ResourceCategory.PART


def test_url_fallback_when_filename_is_empty():
    v = _view("", url="https://example.com/downloads/build.iso")
    assert categorize_resource(v) is ResourceCategory.UNKNOWN


def test_content_type_fallback_for_archive():
    v = _view("", url="https://example.com/payload", content_type="application/zip")
    assert categorize_resource(v) is ResourceCategory.ARCHIVE


def test_unknown_extension_returns_unknown():
    assert categorize_resource(_view("mystery.iso")) is ResourceCategory.UNKNOWN
    assert categorize_resource(_view("payload.bin", content_type="application/octet-stream")) is ResourceCategory.UNKNOWN


def test_missing_filename_and_content_type_returns_unknown():
    v = _view("", url="")
    assert categorize_resource(v) is ResourceCategory.UNKNOWN


def test_part_wins_over_installer_precedence():
    assert categorize_resource(_view("game.part1.exe")) is ResourceCategory.PART


def test_part_wins_over_archive_precedence():
    assert categorize_resource(_view("game.part1.zip")) is ResourceCategory.PART


def test_patch_wins_over_installer_precedence():
    assert categorize_resource(_view("game_patch.exe")) is ResourceCategory.PATCH


def test_patch_wins_over_archive_precedence():
    assert categorize_resource(_view("game_patch.zip")) is ResourceCategory.PATCH


def test_documentation_with_no_extension_and_content_type():
    v = _view("README", content_type="text/plain")
    assert categorize_resource(v) is ResourceCategory.DOCUMENTATION


def test_categorize_resources_preserves_input_order():
    views = [
        _view("setup.exe"),
        _view("game.zip"),
        _view("readme.txt"),
    ]
    out = categorize_resources(views)
    assert out == [
        ResourceCategory.INSTALLER,
        ResourceCategory.ARCHIVE,
        ResourceCategory.DOCUMENTATION,
    ]


def test_categorization_is_non_mutating():
    v = _view("setup.exe", content_type="application/octet-stream")
    snapshot_name = v.file.name
    snapshot_ct = v.file.content_type
    _ = categorize_resource(v)
    assert v.file.name == snapshot_name
    assert v.file.content_type == snapshot_ct


def test_deterministic_classification():
    v = _view("Game-Part1.ZIP")
    a = categorize_resource(v)
    b = categorize_resource(v)
    c = categorize_resource(v)
    assert a == b == c is ResourceCategory.PART


def test_iso_file_without_install_hint_is_unknown():
    assert categorize_resource(_view("data.iso")) is ResourceCategory.UNKNOWN


def test_part_with_iso_extension_is_part():
    assert categorize_resource(_view("disk.part1.iso")) is ResourceCategory.PART


def test_service_pack_zip_is_patch():
    assert categorize_resource(_view("servicepack_2.zip")) is ResourceCategory.PATCH
