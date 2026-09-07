"""Defensive URL validation.

This is a SSRF-style guard, not an access-control bypass. It refuses to
hand obviously unsafe destinations to the resolver/probe pipeline:
    - non-HTTP(S) schemes
    - empty / malformed URLs
    - missing host
    - localhost / loopback / private / link-local / multicast destinations
    - IPv6 loopback / private / link-local / multicast
    - file://, gopher://, etc.

Private-network rejection can be disabled for local development via
allow_private_networks=True (intended for tests only).
"""
import ipaddress
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlsplit


@dataclass
class UrlSecurityDecision:
    is_safe: bool
    reason: str = ""


def _is_private_host(host: str) -> bool:
    if not host:
        return True

    lowered = host.lower()
    if lowered in ("localhost", "localhost.localdomain"):
        return True

    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False

    if isinstance(ip, ipaddress.IPv6Address):
        return (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


_ALLOWED_SCHEMES = frozenset({"http", "https"})


def validate_url(
    url: str,
    *,
    allow_private_networks: bool = False,
    blocked_hosts: Iterable[str] = (),
) -> UrlSecurityDecision:
    if not url or not isinstance(url, str):
        return UrlSecurityDecision(False, "URL is empty or not a string")

    stripped = url.strip()
    if not stripped:
        return UrlSecurityDecision(False, "URL is empty")

    try:
        parts = urlsplit(stripped)
    except Exception:
        return UrlSecurityDecision(False, "URL is malformed")

    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return UrlSecurityDecision(False, f"Unsupported scheme: {scheme or '(none)'}")

    host = (parts.hostname or "").lower()
    if not host:
        return UrlSecurityDecision(False, "URL has no host")

    for blocked in blocked_hosts:
        if blocked and host == blocked.lower():
            return UrlSecurityDecision(False, f"Blocked host: {host}")

    if not allow_private_networks and _is_private_host(host):
        return UrlSecurityDecision(False, f"Private / loopback host rejected: {host}")

    return UrlSecurityDecision(True, "OK")


def is_safe_redirect(
    original_url: str,
    redirect_url: str,
    *,
    allow_private_networks: bool = False,
    blocked_hosts: Iterable[str] = (),
) -> UrlSecurityDecision:
    """Reject redirects that switch scheme or land on a private/blocked host."""
    base_decision = validate_url(
        redirect_url,
        allow_private_networks=allow_private_networks,
        blocked_hosts=blocked_hosts,
    )
    if not base_decision.is_safe:
        return base_decision

    try:
        orig_scheme = (urlsplit(original_url).scheme or "").lower()
        new_scheme = (urlsplit(redirect_url).scheme or "").lower()
    except Exception:
        return UrlSecurityDecision(False, "Redirect URL is malformed")

    if orig_scheme and new_scheme and orig_scheme != new_scheme:
        return UrlSecurityDecision(False, "Redirect changes scheme")

    return UrlSecurityDecision(True, "OK")


def is_safe_url(url: str) -> bool:
    return validate_url(url).is_safe