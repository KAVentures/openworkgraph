"""Transport-level contract: the production Graph opener must never forward bearer tokens.

These tests use only loopback HTTP servers. They exercise the actual urllib
opener configuration rather than a mocked opener or Microsoft credentials.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib import error, request

import pytest

from server.m365_outlook_selected import _opener


@contextmanager
def _server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("redirect_code", [301, 302, 303, 307, 308])
def test_production_opener_never_follows_redirect_with_bearer(redirect_code):
    """No redirect destination may receive a Graph bearer credential."""
    hits = []
    class Destination(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass

    with _server(Destination) as destination:
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                assert self.headers.get("Authorization") == "Bearer sentinel-not-real"
                self.send_response(redirect_code)
                self.send_header("Location", destination + "/capture")
                self.end_headers()
            def log_message(self, *args):
                pass

        with _server(Redirect) as origin:
            req = request.Request(origin + "/graph", headers={
                "Authorization": "Bearer sentinel-not-real",
            })
            with pytest.raises(error.HTTPError) as caught:
                _opener().open(req, timeout=3)
            assert caught.value.code == redirect_code
            caught.value.close()
    assert hits == [], "redirect destination received a request"


def test_production_opener_disables_environment_proxy(monkeypatch):
    """A hostile HTTP_PROXY must not see the bearer credential."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer sentinel-not-real"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args):
            pass
    with _server(Origin) as origin:
        req = request.Request(origin, headers={
            "Authorization": "Bearer sentinel-not-real",
        })
        with _opener().open(req, timeout=3) as response:
            assert response.read() == b"ok"
