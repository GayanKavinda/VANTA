from app.services.url_security import (
    is_safe_redirect,
    is_safe_url,
    validate_url,
)


def test_validate_url_https_is_safe():
    decision = validate_url("https://example.com/file.zip")
    assert decision.is_safe is True


def test_validate_url_http_is_safe():
    decision = validate_url("http://example.com/file.zip")
    assert decision.is_safe is True


def test_validate_url_rejects_ftp():
    decision = validate_url("ftp://example.com/file.zip")
    assert decision.is_safe is False


def test_validate_url_rejects_javascript():
    decision = validate_url("javascript:alert(1)")
    assert decision.is_safe is False


def test_validate_url_rejects_empty():
    assert validate_url("").is_safe is False
    assert validate_url(None).is_safe is False  # type: ignore[arg-type]


def test_validate_url_rejects_malformed():
    decision = validate_url("http://")
    assert decision.is_safe is False


def test_validate_url_rejects_localhost_by_default():
    decision = validate_url("http://localhost/file.zip")
    assert decision.is_safe is False


def test_validate_url_rejects_loopback_ipv4_by_default():
    decision = validate_url("http://127.0.0.1/file.zip")
    assert decision.is_safe is False


def test_validate_url_rejects_private_ipv4_by_default():
    decision = validate_url("http://192.168.1.1/file.zip")
    assert decision.is_safe is False


def test_validate_url_rejects_ipv6_loopback_by_default():
    decision = validate_url("http://[::1]/file.zip")
    assert decision.is_safe is False


def test_validate_url_rejects_ipv6_private_by_default():
    decision = validate_url("http://[fc00::1]/file.zip")
    assert decision.is_safe is False


def test_validate_url_allows_private_when_enabled():
    decision = validate_url(
        "http://127.0.0.1/file.zip",
        allow_private_networks=True,
    )
    assert decision.is_safe is True


def test_validate_url_blocked_host():
    decision = validate_url(
        "https://evil.example.com/file.zip",
        blocked_hosts=("evil.example.com",),
    )
    assert decision.is_safe is False


def test_is_safe_url_helper():
    assert is_safe_url("https://example.com/x") is True
    assert is_safe_url("javascript:alert(1)") is False


def test_is_safe_redirect_same_host_same_scheme():
    decision = is_safe_redirect(
        "https://example.com/a",
        "https://example.com/b",
    )
    assert decision.is_safe is True


def test_is_safe_redirect_rejects_scheme_change():
    decision = is_safe_redirect(
        "https://example.com/a",
        "http://example.com/b",
    )
    assert decision.is_safe is False


def test_is_safe_redirect_rejects_private_destination():
    decision = is_safe_redirect(
        "https://example.com/a",
        "http://127.0.0.1/b",
    )
    assert decision.is_safe is False


def test_is_safe_redirect_rejects_malformed():
    decision = is_safe_redirect(
        "https://example.com/a",
        "not-a-url",
    )
    assert decision.is_safe is False