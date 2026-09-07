from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class IntegrationRequestHandler(BaseHTTPRequestHandler):
    """Small deterministic HTTP server for V1.9 integration tests."""

    server_version = "VANTAIntegration/1.0"

    def do_HEAD(self):
        if self.path == "/game/setup.exe":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "1024")
            self.end_headers()
            return

        if self.path == "/game/game.zip":
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", "2048")
            self.end_headers()
            return

        if self.path == "/game/readme.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "128")
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path == "/page":
            body = b"""
            <html>
                <head>
                    <title>VANTA Integration Test</title>
                </head>
                <body>
                    <a href="/game/setup.exe">Setup</a>
                    <a href="/game/game.zip">Game Archive</a>
                    <a href="/game/readme.txt">Readme</a>
                </body>
            </html>
            """

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/game/setup.exe":
            body = b"S" * 1024
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/game/game.zip":
            body = b"Z" * 2048
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/zip",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/game/readme.txt":
            body = b"VANTA integration test readme."
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/plain",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        """Keep pytest output clean."""
        return


@pytest.fixture
def integration_server():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        IntegrationRequestHandler,
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
