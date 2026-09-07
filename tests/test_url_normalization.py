from app.sources.link_classifier import normalize_url


def test_normalize_lowercases_scheme_and_host():
    assert normalize_url("HTTP://Example.COM/foo") == "http://example.com/foo"


def test_normalize_removes_default_https_port():
    assert normalize_url("https://example.com:443/foo") == "https://example.com/foo"


def test_normalize_removes_default_http_port():
    assert normalize_url("http://example.com:80/foo") == "http://example.com/foo"


def test_normalize_preserves_non_default_port():
    assert normalize_url("http://example.com:8080/foo") == "http://example.com:8080/foo"


def test_normalize_removes_fragment():
    assert normalize_url("https://example.com/foo#section") == "https://example.com/foo"


def test_normalize_removes_empty_fragment():
    assert normalize_url("https://example.com/foo#") == "https://example.com/foo"


def test_normalize_preserves_query_string():
    assert normalize_url("https://example.com/foo?id=1") == "https://example.com/foo?id=1"


def test_normalize_different_query_remains_different():
    a = normalize_url("https://example.com/dl?id=1")
    b = normalize_url("https://example.com/dl?id=2")
    assert a != b


def test_normalize_removes_trailing_slash():
    assert normalize_url("https://example.com/foo/") == "https://example.com/foo"


def test_normalize_keeps_root_path():
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_normalize_handles_empty_path():
    assert normalize_url("https://example.com") == "https://example.com/"


def test_normalize_handles_non_http_scheme_passthrough():
    assert normalize_url("ftp://example.com/x") == "ftp://example.com/x"