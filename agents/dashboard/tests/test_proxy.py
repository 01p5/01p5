"""Tests for the dashboard reverse-proxy helper.

We spin up a tiny upstream HTTP server in a thread, then build a
fake request handler that calls proxy_request and assert on what the
upstream actually received plus what the client got back.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest

from dashboard.proxy import proxy_request


# ---------------------------------------------------------------------------
# Upstream server fixture — records each request, echoes via configured handler
# ---------------------------------------------------------------------------


class _RecordingHandler(BaseHTTPRequestHandler):
    """Records (method, path, headers, body) into the server's `events`
    list, then returns whatever the server.responder callable produces."""

    def log_message(self, *_):  # quiet
        return

    def _record(self) -> tuple[int, dict, bytes]:
        body = b""
        cl = int(self.headers.get("Content-Length") or 0)
        if cl > 0:
            body = self.rfile.read(cl)
        self.server.events.append({  # type: ignore[attr-defined]
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        return self.server.responder(self)  # type: ignore[attr-defined]

    def do_GET(self):
        status, headers, body = self._record()
        self._respond(status, headers, body)

    def do_POST(self):
        status, headers, body = self._record()
        self._respond(status, headers, body)

    def do_DELETE(self):
        status, headers, body = self._record()
        self._respond(status, headers, body)

    def _respond(self, status: int, headers: dict, body: bytes) -> None:
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


@pytest.fixture
def upstream():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    srv.events = []  # type: ignore[attr-defined]
    srv.responder = lambda _: (200, {"Content-Type": "application/json"}, b'{"ok": true}')  # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{srv.server_port}"
    yield srv, base_url
    srv.shutdown()
    thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Fake client handler — drives proxy_request without a real socket
# ---------------------------------------------------------------------------


class _FakeReq:
    """Mimics the bits of BaseHTTPRequestHandler proxy_request reads."""

    def __init__(self, method: str, path: str, headers: dict | None = None, body: bytes = b""):
        self.command = method
        self.path = path
        self.headers = headers or {}
        if body:
            self.headers.setdefault("Content-Length", str(len(body)))
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        # captured by send_response / send_header / end_headers
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, k: str, v: str) -> None:
        self.response_headers[k] = v

    def end_headers(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_strips_prefix_and_forwards(upstream):
    _, base = upstream
    req = _FakeReq("GET", "/slurm/healthz")
    proxy_request(req, base, "/slurm")
    assert req.status == 200
    # Upstream saw the un-prefixed path.
    srv, _ = upstream
    assert srv.events[-1]["path"] == "/healthz"
    assert srv.events[-1]["method"] == "GET"


def test_forwards_post_body_and_content_type(upstream):
    _, base = upstream
    payload = json.dumps({"jsonrpc": "2.0", "method": "initialize"}).encode()
    req = _FakeReq(
        "POST", "/slurm/mcp/local",
        headers={"Content-Type": "application/json"},
        body=payload,
    )
    proxy_request(req, base, "/slurm")
    assert req.status == 200
    srv, _ = upstream
    ev = srv.events[-1]
    assert ev["path"] == "/mcp/local"
    assert ev["body"] == payload
    assert ev["headers"].get("Content-Type") == "application/json"


def test_returns_502_when_upstream_unreachable():
    req = _FakeReq("GET", "/slurm/healthz")
    # Port 1 is reserved + never bound — connection refused.
    proxy_request(req, "http://127.0.0.1:1", "/slurm", timeout=2)
    assert req.status == 502
    body = req.wfile.getvalue()
    payload = json.loads(body.decode())
    assert "upstream unavailable" in payload["error"]
    assert "127.0.0.1:1" in payload["target"]


def test_passes_through_upstream_4xx(upstream):
    srv, base = upstream
    srv.responder = lambda _: (404, {"Content-Type": "application/json"}, b'{"error": "nope"}')  # type: ignore[attr-defined]
    req = _FakeReq("GET", "/slurm/missing")
    proxy_request(req, base, "/slurm")
    assert req.status == 404
    assert b'"nope"' in req.wfile.getvalue()


def test_strips_hop_by_hop_headers_outbound(upstream):
    _, base = upstream
    req = _FakeReq(
        "GET", "/slurm/healthz",
        headers={"Connection": "keep-alive", "Host": "example.com",
                 "X-Foo": "bar", "Transfer-Encoding": "chunked"},
    )
    proxy_request(req, base, "/slurm")
    srv, _ = upstream
    sent = srv.events[-1]["headers"]
    # Caller-supplied Host doesn't reach upstream (urllib substitutes
    # the target's). Caller's Transfer-Encoding doesn't either. Caller's
    # Connection header value was dropped from the urllib.Request — what
    # the upstream sees for Connection comes from urllib itself
    # ("close"), not "keep-alive" the caller sent.
    assert sent.get("Host", "").startswith("127.0.0.1")  # urllib's value
    assert sent.get("Connection") != "keep-alive"
    assert "Transfer-Encoding" not in sent
    assert sent.get("X-Foo") == "bar"  # app header preserved


def test_proxy_root_path_becomes_slash(upstream):
    """Hitting /slurm exactly (no trailing slash) should target /."""
    _, base = upstream
    req = _FakeReq("GET", "/slurm")
    proxy_request(req, base, "/slurm")
    srv, _ = upstream
    assert srv.events[-1]["path"] == "/"


def test_misrouted_path_returns_500():
    """Caller should only invoke proxy_request when path matches the
    prefix. If it doesn't, refuse rather than build a bad URL."""
    req = _FakeReq("GET", "/other/thing")
    proxy_request(req, "http://127.0.0.1:1", "/slurm")
    assert req.status == 500
