from app.core.validators import is_valid_url, is_empty_url, get_url_scheme


def test_is_valid_url_valid():
    assert is_valid_url("https://example.com/file.zip") is True
    assert is_valid_url("http://localhost:8080/test") is True


def test_is_valid_url_invalid():
    assert is_valid_url("") is False
    assert is_valid_url("not a url") is False
    assert is_valid_url("ftp://example.com") is False
    assert is_valid_url("javascript:void(0)") is False


def test_is_empty_url():
    assert is_empty_url("") is True
    assert is_empty_url("   ") is True
    assert is_empty_url("https://example.com") is False


def test_get_url_scheme():
    assert get_url_scheme("https://example.com") == "https"
    assert get_url_scheme("HTtp://example.com") == "http"
    assert get_url_scheme("not-a-url") is None
