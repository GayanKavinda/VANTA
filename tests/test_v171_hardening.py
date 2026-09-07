import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

import pytest

from app.services.analysis_view import build_view_model
from app.services.analyzer import AnalyzerService, ResolutionContext
from app.services.resolver import Resolver
from app.services.resource_probe import ResourceProbe
from app.sources.generic import GenericSourceAdapter
from app.core.models import AnalysisResult, DownloadFile
from app.utils.logger import setup_logger

setup_logger()


def test_resolution_context_for_file_annotation_resolves():
    import inspect
    from app.services.analyzer import ResolutionContext
    sig = inspect.signature(ResolutionContext.for_file)
    assert "file" in sig.parameters
    assert sig.parameters["file"].annotation is not inspect.Parameter.empty


def test_analyzer_imports_downloadfile():
    src = open("app/services/analyzer.py").read()
    assert "DownloadFile" in src


def test_analyzer_for_file_runs_without_nameerror():
    from app.core.models import DownloadFile
    ctx = ResolutionContext()
    f = DownloadFile(name="x.zip", url="https://example.com/x.zip")
    assert ctx.for_file(f) is None


class _RedirectPageHandler(SimpleHTTPRequestHandler):
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
        if path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/page.html")
            self.end_headers()
            return
        if path == "/private":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/file")
            self.end_headers()
            return
        self.send_error(404, "Not found")


@pytest.fixture(scope="module")
def redirect_page_server():
    pages = {
        "/page.html": (
            200, "text/html",
            b"<html><head><title>Title</title></head><body></body></html>",
        ),
    }

    class _H(_RedirectPageHandler):
        pass

    _H.pages = pages

    server = HTTPServer(("127.0.0.1", 19000), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield "http://127.0.0.1:19000"
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_generic_adapter_walks_safe_redirects_manually(redirect_page_server):
    adapter = GenericSourceAdapter(allow_private_networks=True)
    result = await adapter.analyze(f"{redirect_page_server}/redir")
    assert result.status in ("ready", "unsupported")
    assert result.title == "Title"


@pytest.mark.asyncio
async def test_generic_adapter_rejects_redirect_to_private_host(redirect_page_server):
    adapter = GenericSourceAdapter(allow_private_networks=False)
    result = await adapter.analyze(f"{redirect_page_server}/private")
    assert result.status == "error"
    assert "reject" in result.title.lower() or "private" in result.title.lower()


@pytest.mark.asyncio
async def test_generic_adapter_redirect_rejects_unsafe_target():
    import os
    import threading
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    from urllib.parse import urlparse

    class _H(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/x")
            self.end_headers()

    server = HTTPServer(("127.0.0.1", 19001), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        adapter = GenericSourceAdapter(allow_private_networks=False)
        result = await adapter.analyze("http://127.0.0.1:19001/start")
        assert result.status == "error"
    finally:
        server.shutdown()


def test_main_window_prompt_recovery_uses_parameter_not_local():
    import ast
    src = open("app/ui/main_window.py").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_prompt_recovery":
            func_src = ast.unparse(node)
            assert "for task in tasks:" in func_src or "for task in tasks " in func_src
            assert "for task in interrupted_tasks:" not in func_src
            return
    raise AssertionError("_prompt_recovery not found")


def test_home_page_show_analysis_view_no_result_param():
    import ast
    src = open("app/ui/pages/home_page.py").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "show_analysis_view":
            args = [a.arg for a in node.args.args]
            assert args == ["self", "view_model"], f"Got {args}"
            return
    raise AssertionError("show_analysis_view not found")