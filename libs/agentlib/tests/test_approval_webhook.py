"""
Tests for WebhookApprovalHook.

Stand up a real loopback HTTP server to play the role of the human-side
"approval queue" and verify the round-trip:
  agent → hook.request → POST → human service → POST callback → ApprovalDecision.

No external network. Each test gets fresh ephemeral ports.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agentlib import WebhookApprovalHook


class _FakeApprovalQueue:
    """A tiny HTTP server that, on receiving an approval request, calls
    the supplied ``responder(payload)`` to produce a decision and POSTs
    that decision back to ``payload['callback_url']``."""

    def __init__(self, responder):
        self._responder = responder
        self.received: list[dict] = []

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a, **_kw):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.received.append(body)
                self.send_response(202)
                self.end_headers()
                # Respond asynchronously so the original request returns
                # promptly and the hook moves into "wait for callback".
                threading.Thread(
                    target=outer._fire_callback, args=(body,), daemon=True
                ).start()

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://{self._server.server_address[0]}:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _fire_callback(self, payload: dict) -> None:
        decision = self._responder(payload)
        if decision is None:
            return  # simulate human silence (used by timeout test)
        body = json.dumps(decision).encode("utf-8")
        req = urllib.request.Request(
            payload["callback_url"],
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Brief delay so the hook is parked in event.wait() when we POST.
        time.sleep(0.05)
        try:
            urllib.request.urlopen(req, timeout=2.0).read()
        except Exception:
            pass

    def shutdown(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1.0)


@pytest.fixture
def queue_factory():
    queues: list[_FakeApprovalQueue] = []

    def _make(responder):
        q = _FakeApprovalQueue(responder)
        queues.append(q)
        return q

    yield _make
    for q in queues:
        q.shutdown()


def test_webhook_approves_when_callback_returns_true(queue_factory):
    queue = queue_factory(lambda payload: {"approved": True, "reason": "looks good"})
    with WebhookApprovalHook(request_url=queue.url, approval_timeout_seconds=5.0) as hook:
        decision = hook.request(
            agent="terraform",
            tool="tf_apply",
            args={"working_dir": "/tmp/x"},
            rationale="apply the plan",
            diff="Plan: 1 to add",
        )
    assert decision.approved is True
    assert decision.reason == "looks good"
    # The outgoing payload included the diff and a callback URL.
    assert len(queue.received) == 1
    sent = queue.received[0]
    assert sent["agent"] == "terraform"
    assert sent["diff"] == "Plan: 1 to add"
    assert sent["callback_url"].startswith("http://127.0.0.1:")


def test_webhook_rejects_when_callback_returns_false(queue_factory):
    queue = queue_factory(lambda payload: {"approved": False, "reason": "no"})
    with WebhookApprovalHook(request_url=queue.url, approval_timeout_seconds=5.0) as hook:
        decision = hook.request(
            agent="ansible", tool="run_playbook", args={}, rationale="x", diff=None
        )
    assert decision.approved is False
    assert decision.reason == "no"


def test_webhook_passes_modified_args_through(queue_factory):
    queue = queue_factory(
        lambda payload: {
            "approved": True,
            "reason": "scope tightened",
            "modified_args": {"working_dir": "/tmp/safe"},
        }
    )
    with WebhookApprovalHook(request_url=queue.url, approval_timeout_seconds=5.0) as hook:
        decision = hook.request(
            agent="terraform",
            tool="tf_apply",
            args={"working_dir": "/tmp/wide"},
            rationale="apply",
        )
    assert decision.approved is True
    assert decision.modified_args == {"working_dir": "/tmp/safe"}


def test_webhook_times_out_when_human_is_silent(queue_factory):
    queue = queue_factory(lambda payload: None)  # never responds
    with WebhookApprovalHook(request_url=queue.url, approval_timeout_seconds=0.4) as hook:
        decision = hook.request(
            agent="sysadmin", tool="delete_pod", args={"name": "x"}, rationale="r"
        )
    assert decision.approved is False
    assert "timed out" in decision.reason.lower()


def test_webhook_handles_request_post_failure_gracefully():
    # 127.0.0.1:1 is reserved/closed → POST will fail.
    with WebhookApprovalHook(
        request_url="http://127.0.0.1:1/approval",
        approval_timeout_seconds=1.0,
        request_timeout_seconds=0.5,
    ) as hook:
        decision = hook.request(
            agent="terraform", tool="tf_destroy", args={}, rationale="r"
        )
    assert decision.approved is False
    assert "webhook POST failed" in decision.reason
