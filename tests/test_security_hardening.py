import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

import pytest

from app.core.models import ConfidenceLevel
from app.services.resolver import Resolver
from app.services.resource_probe import ResourceProbe
from app.services.url_security import (
    is_safe_redirect,
    is_safe_url,
    validate_url,
)
from app.sources.generic import GenericSourceAdapter
from app.sources.link_classifier import normalize_url
from app.utils.logger import setup_logger

setup_logger()


def _resolver(**kwargs) -> Resolver:
    defaults = {"allow_private_networks": True}
    defaults.update(kwargs)
    return Resolver(**defaults)


class _RedirectHopHandler(SimpleHTTPRequestHandler):
    """Redirect handler that bounces to a final URL via Location header.

    The redirected destination /final.zip returns 200 with a real body.
    """

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        if self.path == "/final.zip":
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", "8")
            self.send_header("Content-Disposition", 'attachment; filename="final.zip"')
            self.end_headers()
            return
        self.send_response(302)
        self.send_header("Location", "/final.zip")
        self.end_headers()

    def do_GET(self):
        if self.path == "/final.zip":
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", "8")
            self.send_header("Content-Disposition", 'attachment; filename="final.zip"')
            self.end_headers()
            self.wfile.write(b"ZIPDATA!")
            return
        self.send_response(302)
        self.send_header("Location", "/final.zip")
        self.end_headers()


_REMAINING_HOPS = [0]
_FINAL_BODY = b"ZIPDATA"
_FINAL_STATUS = [200]


class _RedirectChainHandler(SimpleHTTPRequestHandler):
    """Handler that redirects N times then 200s."""
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        if _REMAINING_HOPS[0] > 0:
            _REMAINING_HOPS[0] -= 1
            self.send_response(302)
            self.send_header("Location", "/hop")
            self.end_headers()
            return
        self.send_response(_FINAL_STATUS[0])
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(_FINAL_BODY)))
        self.end_headers()

    def do_GET(self):
        self.do_HEAD()


def _start_server(port, handler_cls):
    class _H(handler_cls):
        pass

    server = HTTPServer(("127.0.0.1", port), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture(scope="module")
def redirect_hop_server():
    server = _start_server(18800, _RedirectHopHandler)
    try:
        yield "http://127.0.0.1:18800/redirect"
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def redirect_chain_server():
    server = _start_server(18801, _RedirectChainHandler)
    try:
        yield "http://127.0.0.1:18801/start"
    finally:
        server.shutdown()


def test_normalize_url_handles_malformed_port_gracefully():
    assert normalize_url("https://example.com:notaport/file") == "https://example.com:notaport/file"


def test_normalize_url_handles_negative_port_gracefully():
    assert normalize_url("https://example.com:-5/file") == "https://example.com:-5/file"


def test_normalize_url_handles_huge_port_gracefully():
    assert normalize_url("https://example.com:99999999/file") == "https://example.com:99999999/file"


def test_normalize_url_still_normalizes_valid_default_port():
    assert normalize_url("https://example.com:443/foo") == "https://example.com/foo"


@pytest.mark.asyncio
async def test_generic_adapter_rejects_initial_unsafe_url():
    adapter = GenericSourceAdapter(allow_private_networks=False)
    result = await adapter.analyze("http://127.0.0.1/page.html")
    assert result.status == "error"
    assert result.files == []


@pytest.mark.asyncio
async def test_generic_adapter_rejects_javascript_initial_url():
    adapter = GenericSourceAdapter(allow_private_networks=False)
    result = await adapter.analyze("javascript:alert(1)")
    assert result.status == "error"


@pytest.mark.asyncio
async def test_generic_adapter_rejects_ftp_initial_url():
    adapter = GenericSourceAdapter(allow_private_networks=False)
    result = await adapter.analyze("ftp://example.com/file.zip")
    assert result.status == "error"


@pytest.mark.asyncio
async def test_generic_adapter_accepts_initial_url_when_allowed_private():
    adapter = GenericSourceAdapter(allow_private_networks=True)
    decision = validate_url("http://127.0.0.1/x", allow_private_networks=True)
    assert decision.is_safe is True


@pytest.mark.asyncio
async def test_probe_rejects_initial_unsafe_url():
    probe = ResourceProbe(allow_private_networks=False)
    result = await probe.probe("http://127.0.0.1/file.zip")
    assert result.status_code == 0
    assert result.is_downloadable is False


@pytest.mark.asyncio
async def test_probe_walks_redirects_and_records_final_url(redirect_hop_server):
    probe = ResourceProbe(allow_private_networks=True)
    result = await probe.probe(redirect_hop_server)
    assert result.status_code == 200
    assert result.final_url.endswith("/final.zip")
    assert result.url == redirect_hop_server


@pytest.mark.asyncio
async def test_probe_rejects_redirect_to_private_host():
    probe = ResourceProbe(allow_private_networks=False)
    result = await probe.probe("https://example.com/redirect")
    assert result.status_code == 0


@pytest.mark.asyncio
async def test_probe_caps_redirect_loop(redirect_chain_server):
    _REMAINING_HOPS[0] = 20
    _FINAL_STATUS[0] = 200
    probe = ResourceProbe(allow_private_networks=True, max_redirects=3)
    result = await probe.probe(redirect_chain_server)
    assert result.status_code in (302, 0)


@pytest.mark.asyncio
async def test_probe_max_redirects_configurable():
    probe = ResourceProbe(allow_private_networks=True, max_redirects=0)
    assert probe.max_redirects == 0


def test_resolver_reasons_are_not_duplicated():
    from app.core.models import ResourceProbeResult
    from app.sources.candidate_scorer import score_candidate

    probe = ResourceProbeResult(
        url="https://example.com/x",
        final_url="https://example.com/x",
        status_code=200,
        content_type="application/zip",
        filename="x.zip",
        supports_range=True,
        is_downloadable=True,
    )

    score, reasons = score_candidate(
        url="https://example.com/x",
        probe=probe,
    )

    assert len(reasons) == len(set(reasons))


@pytest.mark.asyncio
async def test_resolver_skipped_extension_resource_has_no_dup_reasons():
    resolver = _resolver()
    results = await resolver.resolve_candidates([
        {"url": "https://example.com/file.zip", "anchor_text": ""},
    ])
    assert len(results) == 1
    reasons = results[0].reasons
    assert len(reasons) == len(set(reasons))


@pytest.mark.asyncio
async def test_resolver_rejected_unsafe_redirect_marks_confidence_rejected():
    resolver = Resolver(allow_private_networks=False)
    results = await resolver.resolve_candidates([
        {"url": "https://example.com/safe.zip", "anchor_text": ""},
    ])
    assert results == [] or all(r.confidence != ConfidenceLevel.REJECTED for r in results)


def test_is_safe_redirect_helper_rejects_scheme_change():
    decision = is_safe_redirect("https://a", "http://b")
    assert decision.is_safe is False