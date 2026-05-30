"""Reverse-proxy helper for the dashboard server.

S2.C2 — when Olympus is deployed alongside the sibling slurm-dashboard
and gpu-dashboard pods, requests to ``/slurm/*`` and ``/gpu/*`` are
forwarded to those services so users see them embedded under
``/capabilities/slurm`` / ``/capabilities/gpu`` (iframe) and the
Olympus session cookie protects them.

This is deliberately small + stdlib-only — no httpx/requests dep.
``urllib.request`` handles HTTP/1.1 GET/POST/PATCH/DELETE/PUT, which
is all the sibling dashboards speak. Streaming responses (the dashboards
don't currently use SSE on these routes) would need to be handled
chunk-by-chunk; ``urlopen`` returns a file-like we copy in bounded
chunks so memory stays small even for large asset bundles.
"""
from __future__ import annotations

import json
import logging
import shutil
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

logger = logging.getLogger(__name__)


# Headers we never forward — hop-by-hop or rewriting these would
# corrupt the proxied conversation.
_HOP_BY_HOP = {
    "host", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding",
    "upgrade",
}


def proxy_request(
    req: BaseHTTPRequestHandler,
    target_base: str,
    strip_prefix: str,
    timeout: float = 30.0,
) -> None:
    """Forward ``req`` to ``target_base + (req.path - strip_prefix)``.

    Body, method, and most headers pass through. The proxied response's
    status, headers, and body are written back via ``req``. On upstream
    connect failure, returns 502 with a JSON error body so the
    Olympus session cookie + middleware aren't bypassed.

    Reads request body once (bounded by Content-Length); does NOT
    support chunked request bodies (the sibling APIs don't need that).
    """
    # 1. Build the target URL — strip the prefix so the backend sees
    #    its own native path. e.g. /slurm/healthz → http://slurm-dashboard:8770/healthz.
    raw_path = req.path
    if not raw_path.startswith(strip_prefix):
        # Caller misrouted us; this would otherwise build a bad URL.
        req.send_response(500)
        req.end_headers()
        return
    rest = raw_path[len(strip_prefix):]
    if not rest.startswith("/"):
        rest = "/" + rest
    target = f"{target_base.rstrip('/')}{rest}"

    # 2. Read the body (POST/PATCH/PUT). Bounded by Content-Length.
    body = None
    cl = int(req.headers.get("Content-Length") or 0)
    if cl > 0:
        body = req.rfile.read(cl)

    # 3. Forward headers, dropping hop-by-hop ones. Strip Host because
    #    urllib re-derives it from the target URL.
    forward_headers: dict[str, str] = {}
    for k, v in req.headers.items():
        if k.lower() not in _HOP_BY_HOP:
            forward_headers[k] = v

    out_req = urllib.request.Request(
        target,
        data=body,
        method=req.command,
        headers=forward_headers,
    )

    try:
        with urllib.request.urlopen(out_req, timeout=timeout) as resp:
            _write_proxied_response(req, resp.status, resp.headers, resp)
    except urllib.error.HTTPError as exc:
        # Backend returned non-2xx — still a real response, surface it.
        _write_proxied_response(req, exc.code, exc.headers, exc.fp if exc.fp else None)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        logger.warning("proxy %s %s → 502 upstream: %s", req.command, target, exc)
        payload = json.dumps({
            "error": "upstream unavailable",
            "detail": f"{type(exc).__name__}: {exc}",
            "target": target,
        }).encode("utf-8")
        req.send_response(502)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(payload)))
        req.end_headers()
        req.wfile.write(payload)


def _write_proxied_response(
    req: BaseHTTPRequestHandler,
    status: int,
    headers,
    body_stream,
) -> None:
    req.send_response(status)
    for k, v in headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        req.send_header(k, v)
    req.end_headers()
    if body_stream is not None:
        # 64KB chunks — large enough for HTML/JS assets, small enough
        # to bound memory on a slow client.
        shutil.copyfileobj(body_stream, req.wfile, length=64 * 1024)
