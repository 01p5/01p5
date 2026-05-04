"""
Dashboard backend tests.

End-to-end against a running ``DashboardServer`` on a loopback ephemeral
port. Stubs out the agent layer so no LLM is needed; uses a stub agent
that asks for approval before "completing" so we exercise the approval
queue path too.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Sequence

import pytest
from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    ManualRouter,
    Orchestrator,
    QueueApprovalHook,
    TaskMessage,
)
from dashboard.server import DashboardServer


class _ApprovalSeekingAgent(AgentSpec):
    """Calls the approval hook in handle() and reflects the decision."""
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()
    name = "stub"
    domain = "stub"

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        decision = ctx.approval.request(
            agent=self.name,
            tool="do_thing",
            args={"task_id": task.task_id},
            rationale="testing the approval queue path",
        )
        return AgentResult(
            task_id=task.task_id,
            status="success" if decision.approved else "rejected",
            summary=f"approval: {decision.approved} ({decision.reason})",
            artifacts={"decision": decision.approved},
            cost=CostBreakdown(),
        )


@pytest.fixture
def server():
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus,
        agents=[_ApprovalSeekingAgent()],
        ctx=ctx,
        router=ManualRouter(default="stub"),
        result_timeout_seconds=5.0,
    )
    srv = DashboardServer(
        orchestrator=orch,
        bus=bus,
        approval_hook=approval,
        host="127.0.0.1",
        port=0,
    )
    srv.serve()
    yield srv
    srv.shutdown()


def _get(server, path: str, timeout: float = 2.0) -> tuple[int, dict | str]:
    host, port = server.address
    req = urllib.request.Request(f"http://{host}:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def _post(server, path: str, body: dict, timeout: float = 2.0) -> tuple[int, dict]:
    host, port = server.address
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://{host}:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_healthz(server):
    status, body = _get(server, "/healthz")
    assert status == 200
    assert body == {"ok": True}


def test_static_index_served(server):
    status, body = _get(server, "/")
    assert status == 200
    assert "Olympus" in body


def test_post_task_returns_task_id_and_runs_to_completion(server):
    status, body = _post(server, "/tasks", {"natural_language": "do a thing"})
    assert status == 202
    task_id = body["task_id"]

    # Approval lands on the queue. Resolve it.
    assert _wait(lambda: _get(server, "/approvals")[1] != [])
    _, approvals = _get(server, "/approvals")
    assert len(approvals) == 1
    assert approvals[0]["agent"] == "stub"

    status, body = _post(
        server,
        f"/approvals/{approvals[0]['approval_id']}",
        {"approved": True, "reason": "ok"},
    )
    assert status == 200

    # Task settles to success.
    def _done():
        s, b = _get(server, f"/tasks/{task_id}")
        return s == 200 and b.get("status") == "success"
    assert _wait(_done)


def test_rejected_approval_propagates_to_task_status(server):
    _, body = _post(server, "/tasks", {"natural_language": "x"})
    task_id = body["task_id"]
    assert _wait(lambda: _get(server, "/approvals")[1] != [])
    _, approvals = _get(server, "/approvals")
    _post(
        server,
        f"/approvals/{approvals[0]['approval_id']}",
        {"approved": False, "reason": "no"},
    )
    def _rejected():
        s, b = _get(server, f"/tasks/{task_id}")
        return s == 200 and b.get("status") == "rejected"
    assert _wait(_rejected)


def test_post_task_rejects_missing_natural_language(server):
    status, body = _post(server, "/tasks", {})
    assert status == 400
    assert "natural_language" in body["error"]


def test_resolve_unknown_approval_returns_404(server):
    status, body = _post(
        server, "/approvals/nope", {"approved": True, "reason": "x"}
    )
    assert status == 404


def test_sse_events_stream_includes_task_message(server):
    """Skim /events via raw socket while a task runs; verify a 'task'
    event lands. Raw socket avoids urllib's blocking read-N semantics
    that hang on small SSE frames."""
    import socket

    host, port = server.address
    captured: list[dict] = []
    stop = threading.Event()

    def reader():
        s = socket.create_connection((host, port), timeout=5.0)
        try:
            s.sendall(
                b"GET /events HTTP/1.1\r\nHost: localhost\r\nAccept: text/event-stream\r\n\r\n"
            )
            s.settimeout(0.2)
            buf = b""
            while not stop.is_set():
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    return
                buf += chunk
                # Strip the HTTP response head once we see it.
                if b"\r\n\r\n" in buf:
                    _head, _, buf = buf.partition(b"\r\n\r\n")
                # SSE frames are separated by blank lines.
                while b"\n\n" in buf:
                    block, _, buf = buf.partition(b"\n\n")
                    for line in block.split(b"\n"):
                        line = line.strip()
                        if line.startswith(b"data:"):
                            try:
                                captured.append(json.loads(line[5:].strip()))
                            except json.JSONDecodeError:
                                pass
                    if any(e.get("kind") == "task" for e in captured):
                        return
        finally:
            s.close()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.1)

    _post(server, "/tasks", {"natural_language": "stream test"})

    assert _wait(lambda: _get(server, "/approvals")[1] != [])
    _, approvals = _get(server, "/approvals")
    _post(
        server,
        f"/approvals/{approvals[0]['approval_id']}",
        {"approved": True, "reason": "ok"},
    )

    assert _wait(lambda: any(e.get("kind") == "task" for e in captured))
    stop.set()
    t.join(timeout=2.0)
