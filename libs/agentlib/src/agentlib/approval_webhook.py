"""
WebhookApprovalHook — async approval via an HTTP callback.

Flow:
  1. Agent invokes a destructive tool. Runtime calls
     ``WebhookApprovalHook.request(...)``.
  2. Hook POSTs the approval request to ``request_url`` (e.g. a Slack
     webhook adapter, an internal "approval queue" service). The
     request body includes a unique ``approval_id`` and an absolute
     ``callback_url`` the human-side system must POST back to.
  3. Hook starts a tiny stdlib HTTP server on ``listen_host:listen_port``,
     blocks waiting for the callback, then returns the decision to the
     runtime.

Why stdlib only: agentlib already has a heavy dependency tail
(langchain, litellm…). The approval hook should not pile on Flask /
FastAPI just to receive one POST. ``http.server`` + ``threading`` is
plenty for a single in-flight approval.

Concurrency: one ``WebhookApprovalHook`` instance handles one in-flight
request at a time. Multi-agent orchestration in v1 dispatches
sequentially, so this matches the bus model. When we go async in v2 the
hook becomes a small queue.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

from .spec import ApprovalDecision

logger = logging.getLogger(__name__)


@dataclass
class _PendingApproval:
    approval_id: str
    decision: Optional[ApprovalDecision] = None
    event: threading.Event = field(default_factory=threading.Event)


class _CallbackHandler(BaseHTTPRequestHandler):
    """Receives the human's decision back from the webhook adapter."""

    # Bound by the hook before serve_forever().
    pending: dict[str, _PendingApproval] = {}

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet stdlib logging
        logger.debug("webhook-callback %s - %s", self.address_string(), fmt % args)

    def do_POST(self) -> None:  # noqa: N802 (stdlib name)
        # /callback/<approval_id>
        parts = self.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "callback":
            self.send_response(404)
            self.end_headers()
            return
        approval_id = parts[1]
        pending = self.pending.get(approval_id)
        if pending is None:
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        approved = bool(body.get("approved", False))
        reason = str(body.get("reason", "no reason given"))
        modified = body.get("modified_args")
        if modified is not None and not isinstance(modified, dict):
            self.send_response(400)
            self.end_headers()
            return

        pending.decision = ApprovalDecision(
            approved=approved,
            reason=reason,
            modified_args=modified,
        )
        pending.event.set()
        self.send_response(204)
        self.end_headers()


class WebhookApprovalHook:
    """ApprovalHook that POSTs to a webhook and waits for a callback POST."""

    def __init__(
        self,
        request_url: str,
        callback_base_url: Optional[str] = None,
        listen_host: str = "127.0.0.1",
        listen_port: int = 0,
        request_timeout_seconds: float = 10.0,
        approval_timeout_seconds: float = 600.0,
    ):
        """
        ``request_url``: where to POST the outgoing approval request.
        ``callback_base_url``: the URL the human-side system should POST
          back to. If None, derived from listen_host/listen_port (useful
          for local dev). Override when running behind a tunnel.
        ``listen_port``: 0 binds an ephemeral port (recommended).
        """
        self.request_url = request_url
        self.request_timeout = request_timeout_seconds
        self.approval_timeout = approval_timeout_seconds
        self._pending: dict[str, _PendingApproval] = {}

        # Bind handler-class state to this instance's pending dict so
        # we never share callbacks across hook instances.
        class _Handler(_CallbackHandler):
            pending = self._pending

        self._handler_cls = _Handler
        self._server = HTTPServer((listen_host, listen_port), self._handler_cls)
        bound_host, bound_port = self._server.server_address[:2]
        self._callback_base = callback_base_url or f"http://{bound_host}:{bound_port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="approval-webhook-listener", daemon=True
        )
        self._thread.start()

    @property
    def callback_base(self) -> str:
        return self._callback_base

    @property
    def listen_address(self) -> tuple[str, int]:
        return self._server.server_address[:2]

    def request(
        self,
        agent: str,
        tool: str,
        args: dict[str, Any],
        rationale: str,
        diff: Optional[str] = None,
    ) -> ApprovalDecision:
        approval_id = str(uuid.uuid4())
        pending = _PendingApproval(approval_id=approval_id)
        self._pending[approval_id] = pending

        body = json.dumps(
            {
                "approval_id": approval_id,
                "callback_url": f"{self._callback_base}/callback/{approval_id}",
                "agent": agent,
                "tool": tool,
                "args": args,
                "rationale": rationale,
                "diff": diff,
            }
        ).encode("utf-8")

        try:
            req = urllib.request.Request(
                self.request_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.request_timeout):
                pass
        except (urllib.error.URLError, socket.timeout, ConnectionError) as exc:
            self._pending.pop(approval_id, None)
            return ApprovalDecision(
                approved=False,
                reason=f"webhook POST failed: {type(exc).__name__}: {exc}",
            )

        deadline = time.monotonic() + self.approval_timeout
        remaining = max(0.0, deadline - time.monotonic())
        if not pending.event.wait(timeout=remaining):
            self._pending.pop(approval_id, None)
            return ApprovalDecision(
                approved=False,
                reason=f"approval timed out after {self.approval_timeout:.0f}s",
            )

        decision = pending.decision or ApprovalDecision(
            approved=False, reason="callback delivered no decision"
        )
        self._pending.pop(approval_id, None)
        return decision

    def shutdown(self) -> None:
        """Stop the callback listener. Idempotent."""
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # Context-manager sugar for tests / short-lived runs.
    def __enter__(self) -> "WebhookApprovalHook":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()
