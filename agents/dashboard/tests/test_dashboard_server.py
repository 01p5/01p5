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


def test_static_index_served(tmp_path):
    """Build a server with a tmp static_dir + fake index.html so the
    test doesn't depend on whether the Vite frontend has been built
    locally / in CI. The behavior under test is "GET / serves the
    index.html"."""
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>Olympus dashboard</title>",
    )
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus, agents=[_ApprovalSeekingAgent()], ctx=ctx,
        router=ManualRouter(default="stub"),
        result_timeout_seconds=5.0,
    )
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, static_dir=tmp_path,
    )
    srv.serve()
    try:
        status, body = _get(srv, "/")
        assert status == 200
        assert "Olympus" in body
    finally:
        srv.shutdown()


def test_spa_fallback_unknown_path_serves_index(tmp_path):
    """Any unmatched GET should fall back to index.html so SPA routes
    like /chat or /kubernetes work on hard refresh."""
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>Olympus dashboard</title>",
    )
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus, agents=[_ApprovalSeekingAgent()], ctx=ctx,
        router=ManualRouter(default="stub"),
        result_timeout_seconds=5.0,
    )
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, static_dir=tmp_path,
    )
    srv.serve()
    try:
        for path in ("/chat", "/kubernetes", "/some/unknown/route"):
            status, body = _get(srv, path)
            assert status == 200, f"path {path} returned {status}"
            assert "Olympus" in body
    finally:
        srv.shutdown()


# ---- new: tool catalog + direct invocation ----


from langchain_core.tools import tool as _lc_tool  # noqa: E402


@_lc_tool
def _t_read_thing(name: str = "world") -> str:
    """Read a thing by name."""
    return f"read:{name}"


@_lc_tool
def _t_write_thing(name: str, value: str) -> str:
    """Write a thing. Destructive."""
    return f"wrote:{name}={value}"


class _ToolAgent(AgentSpec):
    """Agent with two real tools — one read-only, one "destructive" —
    for exercising the human-driven tool endpoints."""
    name = "tooly"
    domain = "tooly"
    tools: Sequence[Any] = [_t_read_thing, _t_write_thing]
    destructive_verbs: set[str] = {"_t_write_thing"}

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:  # unused
        raise NotImplementedError


@pytest.fixture
def tools_server():
    from agentlib import AlwaysApprove
    bus = InMemoryBus()
    approval = AlwaysApprove()  # auto-approve so the direct destructive call returns
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus, agents=[_ToolAgent()], ctx=ctx,
        router=ManualRouter(default="tooly"),
        result_timeout_seconds=5.0,
    )
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=QueueApprovalHook(),
        host="127.0.0.1", port=0,
    )
    srv.serve()
    yield srv
    srv.shutdown()


def test_tools_catalog_lists_every_tool_with_destructive_flag(tools_server):
    status, body = _get(tools_server, "/tools")
    assert status == 200
    names = {t["name"]: t for t in body}
    assert set(names) == {"_t_read_thing", "_t_write_thing"}
    assert names["_t_write_thing"]["destructive"] is True
    assert names["_t_read_thing"]["destructive"] is False
    assert names["_t_read_thing"]["agent"] == "tooly"
    assert "properties" in names["_t_read_thing"]["args_schema"]
    assert "name" in names["_t_read_thing"]["args_schema"]["properties"]


def test_invoke_read_only_tool_directly(tools_server):
    status, body = _post(tools_server, "/tools/tooly/_t_read_thing", {"name": "alice"})
    assert status == 200
    assert body["result"] == "read:alice"
    assert body["agent"] == "tooly" and body["tool"] == "_t_read_thing"
    assert body["task_id"].startswith("manual:")


def test_invoke_destructive_tool_auto_approved(tools_server):
    """With AlwaysApprove, the destructive tool goes through cleanly
    and returns the underlying result string."""
    status, body = _post(
        tools_server, "/tools/tooly/_t_write_thing", {"name": "k", "value": "v"}
    )
    assert status == 200
    assert body["result"] == "wrote:k=v"


def test_invoke_unknown_tool_404(tools_server):
    status, body = _post(tools_server, "/tools/tooly/no_such", {})
    assert status == 404
    assert "unknown tool" in body["error"]


def test_invoke_unknown_agent_404(tools_server):
    status, body = _post(tools_server, "/tools/ghost/anything", {})
    assert status == 404
    assert "unknown agent" in body["error"]


def test_stacks_endpoints_return_lists(tools_server):
    # Returns empty lists when infra dirs are not at the expected
    # locations (no repo mounted in test env) — at minimum, must be JSON arrays.
    s1, b1 = _get(tools_server, "/stacks/terraform")
    s2, b2 = _get(tools_server, "/stacks/ansible")
    assert s1 == 200 and isinstance(b1, list)
    assert s2 == 200 and isinstance(b2, list)


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
