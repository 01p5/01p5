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
from pathlib import Path
from typing import Any, Sequence

import pytest
from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    InMemoryMemoryStore,
    ManualRouter,
    MemoryEntry,
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


# ---------------------------------------------------------------------------
# New edge-case coverage
# ---------------------------------------------------------------------------


def _make_server(static_dir=None, audit_log_path=None, agent=None):
    """Construct a one-off DashboardServer for an edge-case test.
    Caller is responsible for calling .serve() and .shutdown()."""
    from agentlib import AlwaysApprove
    agents = [agent] if agent is not None else [_ApprovalSeekingAgent()]
    router_default = agents[0].name
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus, agents=agents, ctx=ctx,
        router=ManualRouter(default=router_default),
        result_timeout_seconds=5.0,
    )
    kwargs: dict = dict(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0,
    )
    if static_dir is not None:
        kwargs["static_dir"] = static_dir
    if audit_log_path is not None:
        kwargs["audit_log_path"] = audit_log_path
    srv = DashboardServer(**kwargs)
    return srv


def test_spa_fallback_does_not_eat_known_api_endpoints(tmp_path):
    """The SPA fallback must only catch UNKNOWN paths; known endpoints
    like /tools, /tasks, /events, /approvals, /audit, /healthz,
    /stacks/* must NOT return the fake index.html body."""
    fake_index = "<!doctype html><title>OLYMPUS_FAKE_INDEX_MARKER</title>"
    (tmp_path / "index.html").write_text(fake_index)
    srv = _make_server(static_dir=tmp_path, agent=_ToolAgent())
    srv.serve()
    try:
        # JSON endpoints — body should be JSON, not the fake index.
        for path in ("/tools", "/tasks", "/approvals",
                      "/healthz", "/stacks/terraform", "/stacks/ansible"):
            status, body = _get(srv, path)
            assert status == 200, f"{path} returned {status}"
            # Either parsed-as-JSON (dict/list) or a raw string that is
            # NOT the fake index body.
            if isinstance(body, str):
                assert "OLYMPUS_FAKE_INDEX_MARKER" not in body, (
                    f"{path} fell through to SPA fallback"
                )
            else:
                assert isinstance(body, (dict, list))

        # /audit: served as a static file with allow_missing=True.
        # Body should be empty (not the index), status 200.
        status, body = _get(srv, "/audit")
        assert status == 200
        if isinstance(body, str):
            assert "OLYMPUS_FAKE_INDEX_MARKER" not in body
    finally:
        srv.shutdown()


def test_static_serve_path_traversal_returns_404(tmp_path):
    """Requesting a path that resolves outside static_dir must 404, not
    serve the escape file content."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<title>real</title>")
    escape = tmp_path / "escape.txt"
    escape.write_text("SECRET_THAT_MUST_NOT_LEAK")

    srv = _make_server(static_dir=static_dir, agent=_ToolAgent())
    srv.serve()
    try:
        # /../escape.txt — server canonicalizes and rejects. urllib will
        # normalize the path before sending, so use a raw socket.
        import socket
        host, port = srv.address
        for raw_path in ("/../escape.txt", "/static/../escape.txt"):
            with socket.create_connection((host, port), timeout=2.0) as s:
                s.sendall(
                    f"GET {raw_path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
                )
                s.settimeout(2.0)
                buf = b""
                while True:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    if b"\r\n\r\n" in buf and len(buf) > 64:
                        break
                response = buf.decode("utf-8", errors="replace")
                assert "SECRET_THAT_MUST_NOT_LEAK" not in response, (
                    f"path-traversal leaked for {raw_path}"
                )
    finally:
        srv.shutdown()


def test_audit_log_missing_file_returns_empty_body(tmp_path):
    """When audit_log_path doesn't exist, GET /audit must still 200
    (allow_missing=True path) with empty body."""
    missing = tmp_path / "does_not_exist.jsonl"
    assert not missing.exists()
    srv = _make_server(audit_log_path=str(missing))
    srv.serve()
    try:
        status, body = _get(srv, "/audit")
        assert status == 200
        # Empty body: parsed-JSON path raises so we get the raw string ""
        assert body == "" or body == {}
    finally:
        srv.shutdown()


def test_tools_catalog_entry_shape(tools_server):
    """Each /tools entry has the documented set of keys and the
    args_schema is a JSON-schema object."""
    status, body = _get(tools_server, "/tools")
    assert status == 200
    required = {"agent", "name", "description", "args_schema", "destructive"}
    for entry in body:
        assert required.issubset(entry.keys()), (
            f"missing keys: {required - set(entry.keys())}"
        )
    # Specifically for _t_read_thing.
    entry = next(e for e in body if e["name"] == "_t_read_thing")
    schema = entry["args_schema"]
    assert isinstance(schema, dict)
    assert "properties" in schema
    # langchain @tool generates Pydantic models with type: object.
    assert schema.get("type") == "object"


def test_invoke_tool_non_object_body_returns_400(tools_server):
    """A JSON array (or any non-object) for the tool args body is 400."""
    import urllib.error
    import urllib.request
    host, port = tools_server.address
    req = urllib.request.Request(
        f"http://{host}:{port}/tools/tooly/_t_read_thing",
        data=b"[1, 2, 3]",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = json.loads(exc.read().decode("utf-8"))
    assert status == 400
    assert "object" in body["error"]


def test_invoke_tool_invalid_json_returns_400(tools_server):
    """Malformed JSON body to the invoke endpoint returns 400 with
    an error message about invalid JSON."""
    import urllib.error
    import urllib.request
    host, port = tools_server.address
    req = urllib.request.Request(
        f"http://{host}:{port}/tools/tooly/_t_read_thing",
        data=b"{not valid json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = json.loads(exc.read().decode("utf-8"))
    assert status == 400
    assert "JSON" in body["error"] or "json" in body["error"]


def test_invoke_tool_underlying_raises_returns_500_with_task_id():
    """When the underlying tool raises, the server returns 500 with
    {task_id, error} so the UI can correlate the failure."""
    from langchain_core.tools import tool as _lc_tool

    @_lc_tool
    def _boom(reason: str = "kaboom") -> str:
        """Always raises."""
        raise RuntimeError(f"intentional: {reason}")

    class _BoomAgent(AgentSpec):
        name = "boomy"
        domain = "boomy"
        tools: Sequence[Any] = [_boom]
        destructive_verbs: set[str] = set()

        def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
            raise NotImplementedError

    srv = _make_server(agent=_BoomAgent())
    srv.serve()
    try:
        status, body = _post(srv, "/tools/boomy/_boom", {"reason": "test"})
        assert status == 500
        assert "task_id" in body
        assert body["task_id"].startswith("manual:")
        assert "error" in body
        assert "RuntimeError" in body["error"]
    finally:
        srv.shutdown()


def test_stacks_terraform_returns_sorted_list_when_populated(tools_server, monkeypatch, tmp_path):
    """When two .tf files exist under a stack, the endpoint returns
    a sorted list of stack paths. Uses monkeypatch on _infra_roots to
    point at a tmp_path with two stack dirs."""
    # Build a fake infra/terraform tree: <tmp_path>/terraform/{stack-b,stack-a}/main.tf
    tf_root = tmp_path / "terraform"
    (tf_root / "stack-b").mkdir(parents=True)
    (tf_root / "stack-a").mkdir(parents=True)
    (tf_root / "stack-b" / "main.tf").write_text("# stack b\n")
    (tf_root / "stack-a" / "main.tf").write_text("# stack a\n")

    # Patch the server's _infra_roots to return our fake root.
    monkeypatch.setattr(
        tools_server, "_infra_roots",
        lambda kind: [tf_root] if kind == "terraform" else [],
    )
    status, body = _get(tools_server, "/stacks/terraform")
    assert status == 200
    assert isinstance(body, list)
    # Stacks reported relative to root.parent (i.e. tmp_path).
    assert body == sorted(body), f"expected sorted, got {body}"
    # Both stacks present.
    names = [Path(p).name for p in body]
    assert "stack-a" in names and "stack-b" in names


# ---------------------------------------------------------------------
# /memory endpoint
# ---------------------------------------------------------------------


def _memory_server(memory):
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=2.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus,
        agents=[_ApprovalSeekingAgent()],
        ctx=ctx,
        router=ManualRouter(default="stub"),
        result_timeout_seconds=2.0,
        memory=memory,
    )
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0,
    )
    srv.serve()
    return srv


def _entry(task_id="T1", agent="sysadmin", nl="list pods", summary="ok"):
    return MemoryEntry(
        task_id=task_id, agent=agent,
        natural_language=nl, summary=summary, status="success",
    )


def test_memory_endpoint_returns_recent_entries_without_query():
    mem = InMemoryMemoryStore()
    mem.write(_entry(task_id="T1", nl="delete pod web"))
    mem.write(_entry(task_id="T2", nl="run terraform plan"))
    srv = _memory_server(mem)
    try:
        status, body = _get(srv, "/memory")
        assert status == 200
        ids = [e["task_id"] for e in body["entries"]]
        # Most-recent first.
        assert ids == ["T2", "T1"]
    finally:
        srv.shutdown()


def test_memory_endpoint_search_query_filters_by_similarity():
    mem = InMemoryMemoryStore()
    mem.write(_entry(task_id="T1", nl="delete pod web in default"))
    mem.write(_entry(task_id="T2", nl="run terraform plan in pve"))
    srv = _memory_server(mem)
    try:
        status, body = _get(srv, "/memory?q=delete+pod+nginx&k=1")
        assert status == 200
        assert [e["task_id"] for e in body["entries"]] == ["T1"]
    finally:
        srv.shutdown()


def test_memory_endpoint_agent_filter():
    mem = InMemoryMemoryStore()
    mem.write(_entry(task_id="T1", agent="sysadmin", nl="delete pod"))
    mem.write(_entry(task_id="T2", agent="programmer", nl="write dockerfile"))
    srv = _memory_server(mem)
    try:
        status, body = _get(srv, "/memory?agent=programmer")
        assert status == 200
        agents = {e["agent"] for e in body["entries"]}
        assert agents == {"programmer"}
    finally:
        srv.shutdown()


def test_memory_endpoint_returns_empty_when_no_memory_store():
    """Orchestrator without memory= must still respond 200 with an
    empty entries list, not 500."""
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=2.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus, agents=[_ApprovalSeekingAgent()], ctx=ctx,
        router=ManualRouter(default="stub"), result_timeout_seconds=2.0,
    )
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0,
    )
    srv.serve()
    try:
        status, body = _get(srv, "/memory")
        assert status == 200
        # NullMemoryStore.search returns []. The endpoint serves the
        # raw store's _entries list when q is absent — NullMemoryStore
        # doesn't have one, so the fallback search("task", ...) yields [].
        assert body["entries"] == []
    finally:
        srv.shutdown()
