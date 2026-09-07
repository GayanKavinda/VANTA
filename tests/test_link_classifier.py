from app.sources.link_classifier import (
    deduplicate_preserve_order,
    extract_filename_from_url,
    has_download_extension,
    is_download_candidate,
    is_fragment_only,
    is_ignored_scheme,
)
import app.sources.link_classifier as lc


def test_is_ignored_scheme_javascript():
    assert is_ignored_scheme("javascript:void(0)") is True


def test_is_ignored_scheme_mailto():
    assert is_ignored_scheme("mailto:test@example.com") is True


def test_is_ignored_scheme_tel():
    assert is_ignored_scheme("tel:+1234567890") is True


def test_is_ignored_scheme_data():
    assert is_ignored_scheme("data:text/plain;base64,SGk=") is True


def test_is_ignored_scheme_https():
    assert is_ignored_scheme("https://example.com/file.zip") is False


def test_is_ignored_scheme_empty():
    assert is_ignored_scheme("") is True


def test_is_fragment_only_true():
    assert is_fragment_only("#section") is True
    assert is_fragment_only("") is True


def test_is_fragment_only_false():
    assert is_fragment_only("https://example.com/page#section") is False


def test_extract_filename_simple():
    assert extract_filename_from_url("https://example.com/files/game.zip") == "game.zip"


def test_extract_filename_with_query():
    assert extract_filename_from_url("https://example.com/dl?id=1&name=test.zip") == "dl"


def test_extract_filename_with_encoded_path():
    assert extract_filename_from_url("https://example.com/files/my%20archive.zip") == "my archive.zip"


def test_extract_filename_no_path():
    assert extract_filename_from_url("https://example.com") is None


def test_has_download_extension_zip():
    assert has_download_extension("https://example.com/game.zip") is True


def test_has_download_extension_rar():
    assert has_download_extension("https://example.com/file.rar") is True


def test_has_download_extension_7z():
    assert has_download_extension("https://example.com/archive.7z") is True


def test_has_download_extension_iso():
    assert has_download_extension("https://example.com/disk.iso") is True


def test_has_download_extension_exe():
    assert has_download_extension("https://example.com/setup.exe") is True


def test_has_download_extension_pdf():
    assert has_download_extension("https://example.com/manual.pdf") is True


def test_has_download_extension_mp4():
    assert has_download_extension("https://example.com/clip.mp4") is True


def test_has_download_extension_uppercase():
    assert has_download_extension("https://example.com/Game.ZIP") is True


def test_has_download_extension_no_extension():
    assert has_download_extension("https://example.com/about") is False
    assert has_download_extension("https://example.com/page") is False


def test_is_download_candidate_zip():
    assert is_download_candidate("https://example.com/game.zip") is True


def test_is_download_candidate_webpage():
    assert is_download_candidate("https://example.com/about") is False


def test_is_download_candidate_javascript():
    assert is_download_candidate("javascript:void(0)") is False


def test_is_download_candidate_mailto():
    assert is_download_candidate("mailto:x@y.com") is False


def test_is_download_candidate_fragment():
    assert is_download_candidate("#top") is False


def test_is_download_candidate_empty():
    assert is_download_candidate("") is False


def test_deduplicate_preserve_order():
    urls = [
        "https://a.com/x.zip",
        "https://b.com/y.zip",
        "https://a.com/x.zip",
    ]
    assert deduplicate_preserve_order(urls) == [
        "https://a.com/x.zip",
        "https://b.com/y.zip",
    ]


def test_deduplicate_keeps_order_with_dupes():
    urls = ["a", "b", "a", "c", "b", "d"]
    assert deduplicate_preserve_order(urls) == ["a", "b", "c", "d"]


def test_deduplicate_skips_empty():
    urls = ["", "a", "", "b"]
    assert deduplicate_preserve_order(urls) == ["a", "b"]


def test_normalize_url_exists_and_normalizes():
    from app.sources.link_classifier import normalize_url

    assert normalize_url("HTTP://Example.com:80/foo/") == "http://example.com/foo"
    assert normalize_url("https://example.com:443/bar/#frag") == "https://example.com/bar"
    assert normalize_url("http://example.com/file.zip#download") == "http://example.com/file.zip"
    assert normalize_url("https://example.com/dl?id=1&x=2").startswith("https://example.com/dl?")