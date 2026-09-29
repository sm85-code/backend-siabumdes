"""fetch_logo_bytes() must reject non-image responses (e.g. a Google Drive
"can't scan this file for viruses" interstitial page) instead of silently
embedding that HTML into a PDF/Excel/Word report as if it were the logo."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from modules.siabumdes.report_branding import fetch_logo_bytes

_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8ffff3f0005fe02fea739663d0000000049454e"
    "44ae426082"
)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 -- silence test server logs
        pass

    def do_GET(self):
        if self.path == "/logo.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(_PNG_1PX)
        elif self.path == "/interstitial.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>Google Drive can't scan this file for viruses.</html>")
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def logo_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


def test_fetch_logo_bytes_returns_bytes_for_a_real_image(logo_server):
    result = fetch_logo_bytes(f"{logo_server}/logo.png")
    assert result == _PNG_1PX


def test_fetch_logo_bytes_rejects_html_interstitial(logo_server):
    result = fetch_logo_bytes(f"{logo_server}/interstitial.html")
    assert result is None


def test_fetch_logo_bytes_returns_none_for_empty_url():
    assert fetch_logo_bytes("") is None


def test_fetch_logo_bytes_returns_none_on_connection_error():
    assert fetch_logo_bytes("http://127.0.0.1:1/unreachable", timeout=0.5) is None
