import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

import pytest

from app.core.models import ConfidenceLevel
from app.services.resolver import Resolver
from app.services.resource_probe import ResourceProbe
from app.utils.logger import setup_logger

setup_logger()


class _ResolverHandler(SimpleHTTPRequestHandler):
    pages: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path in self.pages:
            status, ctype, body = self.pages[path]
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404, "Not found")

    def do_HEAD(self):
        path = urlparse(self.path).path
        if path.endswith(".zip"):
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", "100")
            self.send_header("Content-Disposition", 'attachment; filename="x.zip"')
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return
        if path == "/download":
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", "200")
            self.send_header("Content-Disposition", 'attachment; filename="game.zip"')
            self.end_headers()
            return
        self.send_error(404)


@pytest.fixture(scope="module")
def resolver_server():
    pages = {
        "/page.html": (
            200,
            "text/html",
            b'<html><body><a href="/download?id=1">Game</a></body></html>',
        ),
    }

    class _H(_ResolverHandler):
        pass

    _H.pages = pages

    server = HTTPServer(("127.0.0.1", 18700), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield "http://127.0.0.1:18700"
    finally:
        server.shutdown()


def _resolver(**kwargs) -> Resolver:
    probe = kwargs.pop("probe", None) or ResourceProbe(allow_private_networks=True)
    defaults = {"allow_private_networks": True, "probe": probe}
    defaults.update(kwargs)
    return Resolver(**defaults)


@pytest.mark.asyncio
async def test_resolver_skips_probe_for_strong_extension_url():
    resolver = _resolver()
    results = await resolver.resolve_candidates([
        {"url": "https://example.com/file.zip", "anchor_text": ""},
    ])
    assert len(results) == 1
    assert results[0].confidence == ConfidenceLevel.HIGH
    assert results[0].filename == "file.zip"


@pytest.mark.asyncio
async def test_resolver_probes_ambiguous_url(resolver_server):
    resolver = _resolver()
    results = await resolver.resolve_candidates([
        {"url": f"{resolver_server}/download?id=1", "anchor_text": "Game"},
    ])
    assert len(results) == 1
    assert results[0].confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)
    assert results[0].filename == "game.zip"
    assert results[0].size == 200


@pytest.mark.asyncio
async def test_resolver_deduplicates_normalized_urls():
    resolver = _resolver()
    results = await resolver.resolve_candidates([
        {"url": "https://Example.com:443/file.zip#download", "anchor_text": ""},
        {"url": "https://example.com/file.zip/", "anchor_text": ""},
        {"url": "https://example.com/file.zip", "anchor_text": ""},
    ])
    assert len(results) == 1


@pytest.mark.asyncio
async def test_resolver_rejects_unsafe_url():
    resolver = Resolver()
    results = await resolver.resolve_candidates([
        {"url": "http://127.0.0.1/file.zip", "anchor_text": ""},
        {"url": "javascript:alert(1)", "anchor_text": ""},
        {"url": "ftp://example.com/x", "anchor_text": ""},
    ])
    assert results == []


@pytest.mark.asyncio
async def test_resolver_preserves_query_differences():
    resolver = _resolver()
    results = await resolver.resolve_candidates([
        {"url": "https://example.com/file.zip?id=1", "anchor_text": ""},
        {"url": "https://example.com/file.zip?id=2", "anchor_text": ""},
    ])
    assert len(results) == 2


@pytest.mark.asyncio
async def test_resolver_probe_failure_does_not_crash():
    resolver = _resolver()
    results = await resolver.resolve_candidates([
        {"url": "http://127.0.0.1:1/nothing", "anchor_text": ""},
    ])
    assert results == []


@pytest.mark.asyncio
async def test_resolver_handles_bounded_concurrency():
    resolver = _resolver(concurrency=2)
    candidates = [
        {"url": f"{resolver._probe is not None}", "anchor_text": ""},
    ]
    results = await resolver.resolve_candidates([
        {"url": "https://example.com/a.zip", "anchor_text": ""},
        {"url": "https://example.com/b.zip", "anchor_text": ""},
        {"url": "https://example.com/c.zip", "anchor_text": ""},
        {"url": "https://example.com/d.zip", "anchor_text": ""},
    ])
    assert len(results) == 4


@pytest.mark.asyncio
async def test_resolver_returns_explainable_reasons(resolver_server):
    resolver = _resolver()
    results = await resolver.resolve_candidates([
        {"url": f"{resolver_server}/download?id=1", "anchor_text": "Download"},
    ])
    assert len(results) == 1
    assert len(results[0].reasons) > 0
    assert any("MIME" in r or "Content-Disposition" in r or "extension" in r for r in results[0].reasons)


@pytest.mark.asyncio
async def test_resolver_rejects_blocked_hosts():
    resolver = Resolver(blocked_hosts=("evil.example.com",))
    results = await resolver.resolve_candidates([
        {"url": "https://evil.example.com/file.zip", "anchor_text": ""},
    ])
    assert results == []


def test_resolver_classify_only_helper():
    resolver = _resolver()
    score, confidence = resolver.classify_only("https://example.com/game.zip")
    assert score >= 90
    assert confidence == ConfidenceLevel.HIGH
    score2, conf2 = resolver.classify_only("https://example.com/about")
    assert conf2 == ConfidenceLevel.REJECTED