"""
Group-chat ticket endpoints + wiring (Phase 2).

Live HTTP against a DashboardServer with a fake main agent (no LLM), plus
direct unit coverage of the ticket SSE helpers and build_default_server
registration.
"""
from __future__ import annotations

import json
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
    InMemoryTicketStore,
    ManualRouter,
    Orchestrator,
    QueueApprovalHook,
    TaskMessage,
    TicketEvent,
)
from dashboard.server import (
    DashboardServer,
    _send_sse_ticket_event,
    build_default_server,
)


class _FakeMain(AgentSpec):
    """Stand-in coordinator: optionally dispatches a specialist via the
    injected ctx.dispatcher, then replies."""

    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()
    name = "main"
    domain = "coordinator"
    routable = False

    def __init__(self, dispatch_to_agent: str | None = None, raise_exc: bool = False):
        self._dispatch = dispatch_to_agent
        self._raise = raise_exc

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        if self._raise:
            raise RuntimeError("main boom")
        note = ""
        if self._dispatch is not None and ctx.dispatcher is not None:
            note = " | " + ctx.dispatcher(self._dispatch, "do the thing")
        return AgentResult(
            task_id=task.task_id,
            status="success",
            summary=f"main reply{note}",
            artifacts={"resolved": True},
            cost=CostBreakdown(),
        )


class _FakeWorker(AgentSpec):
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()
    name = "worker"
    domain = "worker"

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, status="success",
            summary=f"worker handled {task.natural_language!r}", cost=CostBreakdown(),
        )


def _make_server(agents, ticket_store):
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus, agents=agents, ctx=ctx,
        router=ManualRouter(default=agents[0].name),
        ticket_store=ticket_store, result_timeout_seconds=5.0,
    )
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, ticket_store=ticket_store,
    )
    srv.serve()
    return srv


@pytest.fixture
def ticket_server():
    store = InMemoryTicketStore()
    srv = _make_server([_FakeMain(dispatch_to_agent="worker"), _FakeWorker()], store)
    yield srv, store
    srv.shutdown()


def _get(server, path, timeout=2.0):
    host, port = server.address
    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def _post(server, path, body, timeout=2.0):
    host, port = server.address
    req = urllib.request.Request(
        f"http://{host}:{port}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# ---- HTTP endpoint behavior ----

def test_post_message_records_human_main_and_dispatch(ticket_server):
    srv, store = ticket_server
    status, body = _post(srv, "/tickets/T1/messages", {"message": "hello team"})
    assert status == 202
    assert body["ticket_id"] == "T1"

    # human_message immediately; the rest after the worker thread runs.
    assert _wait(lambda: len(store.transcript("T1")) >= 4)
    kinds = [e.kind for e in store.transcript("T1")]
    # human_message, then main's dispatch -> worker, dispatch+agent_result,
    # then main's own agent_message reply.
    assert kinds[0] == "human_message"
    assert "dispatch" in kinds and "agent_result" in kinds
    assert kinds[-1] == "agent_message"
    evs = store.transcript("T1")
    assert evs[0].payload["text"] == "hello team"
    assert evs[-1].actor == "main"
    assert "main reply" in evs[-1].payload["text"]


def test_get_ticket_returns_transcript(ticket_server):
    srv, store = ticket_server
    store.append(TicketEvent(ticket_id="T9", actor="human", kind="human_message", payload={"text": "hi"}))
    status, body = _get(srv, "/tickets/T9")
    assert status == 200
    assert body["ticket_id"] == "T9"
    assert len(body["events"]) == 1
    assert body["events"][0]["kind"] == "human_message"


def test_get_unknown_ticket_returns_empty(ticket_server):
    srv, _ = ticket_server
    status, body = _get(srv, "/tickets/does-not-exist")
    assert status == 200
    assert body["events"] == []


def test_list_tickets_endpoint(ticket_server):
    srv, store = ticket_server
    store.append(TicketEvent(ticket_id="S1", actor="human", kind="human_message", payload={"text": "first task"}))
    store.append(TicketEvent(ticket_id="S2", actor="human", kind="human_message", payload={"text": "second task"}))
    status, body = _get(srv, "/tickets")
    assert status == 200
    ids = {t["ticket_id"] for t in body["tickets"]}
    assert ids == {"S1", "S2"}
    assert any(t["first_message"] == "first task" for t in body["tickets"])


def test_list_tickets_404_when_disabled():
    srv = _make_server([_FakeMain()], None)
    try:
        assert _get(srv, "/tickets")[0] == 404
    finally:
        srv.shutdown()


def test_post_message_requires_message(ticket_server):
    srv, _ = ticket_server
    status, body = _post(srv, "/tickets/T1/messages", {"nope": 1})
    assert status == 400
    assert "message required" in body["error"]


def test_main_failure_recorded_as_agent_message(ticket_server):
    store = InMemoryTicketStore()
    srv = _make_server([_FakeMain(raise_exc=True)], store)
    try:
        _post(srv, "/tickets/TF/messages", {"message": "go"})
        assert _wait(lambda: len(store.transcript("TF")) >= 2)
        last = store.transcript("TF")[-1]
        assert last.kind == "agent_message"
        assert last.payload["status"] == "failed"
        assert "main agent error" in last.payload["text"]
    finally:
        srv.shutdown()


def test_endpoints_404_when_group_chat_disabled():
    # ticket_store=None => group chat off.
    srv = _make_server([_FakeMain()], None)
    try:
        assert _post(srv, "/tickets/T1/messages", {"message": "x"})[0] == 404
        assert _get(srv, "/tickets/T1")[0] == 404
        assert _post(srv, "/tickets/T1/close", {})[0] == 404
    finally:
        srv.shutdown()


def test_close_ticket_endpoint_records_closure(ticket_server):
    srv, store = ticket_server
    _post(srv, "/tickets/TC/messages", {"message": "do it"})
    assert _wait(lambda: len(store.transcript("TC")) >= 2)

    status, body = _post(srv, "/tickets/TC/close", {})
    assert status == 200
    assert body["ticket_id"] == "TC"
    assert "summary" in body
    last = store.transcript("TC")[-1]
    assert last.kind == "agent_message"
    assert last.payload["status"] == "closed"
    assert "Ticket closed" in last.payload["text"]


# ---- direct unit coverage of helpers + wiring ----

def test_submit_ticket_raises_without_store():
    srv = _make_server([_FakeMain()], None)
    try:
        with pytest.raises(RuntimeError, match="group chat disabled"):
            srv.submit_ticket("T1", "hi")
    finally:
        srv.shutdown()


class _FakeReq:
    """Minimal BaseHTTPRequestHandler stand-in for SSE helper tests."""

    def __init__(self, fail_on_data=False):
        self.written: list[bytes] = []
        self._fail = fail_on_data
        self.wfile = self
        self.code = None

    def send_response(self, code): self.code = code
    def send_header(self, *_): pass
    def end_headers(self): pass

    def write(self, b: bytes):
        self.written.append(b)
        if self._fail and b.startswith(b"data:"):
            raise BrokenPipeError()

    def flush(self): pass


def test_send_sse_ticket_event_serializes_event():
    req = _FakeReq()
    ev = TicketEvent(ticket_id="T1", actor="main", kind="agent_message", payload={"text": "hi"}, seq=3)
    assert _send_sse_ticket_event(req, ev) is True
    blob = b"".join(req.written).decode()
    assert blob.startswith("data: ")
    assert "agent_message" in blob and "\"seq\": 3" in blob


def test_send_sse_ticket_event_returns_false_on_disconnect():
    req = _FakeReq(fail_on_data=True)
    ev = TicketEvent(ticket_id="T1", actor="main", kind="agent_message", payload={})
    assert _send_sse_ticket_event(req, ev) is False


def test_serve_ticket_sse_replays_then_returns_on_disconnect():
    store = InMemoryTicketStore()
    store.append(TicketEvent(ticket_id="T1", actor="human", kind="human_message", payload={"text": "hi"}))
    srv = _make_server([_FakeMain()], store)
    try:
        req = _FakeReq(fail_on_data=True)  # disconnect on first data line
        srv._serve_ticket_sse(req, "T1")   # must return, not loop forever
        assert any(b"human_message" in w for w in req.written)
    finally:
        srv.shutdown()


def test_serve_ticket_sse_404_when_disabled():
    srv = _make_server([_FakeMain()], None)
    try:
        req = _FakeReq()
        srv._serve_ticket_sse(req, "T1")
        assert req.code == 404
    finally:
        srv.shutdown()


def test_build_default_server_registers_main_non_routable(tmp_path, monkeypatch):
    monkeypatch.setenv("OLYMPUS_MEMORY", "disabled")
    monkeypatch.setenv("OLYMPUS_ROLLBACK", "disabled")
    srv = build_default_server(audit_log_path=str(tmp_path / "audit.jsonl"))
    assert srv.ticket_store is not None
    # main is a dispatch target...
    assert "main" in srv.orchestrator.agents
    # ...but excluded from the router catalog; specialists remain.
    catalog = getattr(srv.orchestrator.router, "agent_descriptions", {})
    assert "main" not in catalog
    assert "sysadmin" in catalog
