"""
TERM.2a — REST endpoints for terminal sessions.

End-to-end via DashboardServer on a loopback port. SessionManager is
constructed with the dashboard but uses ``/bin/cat`` as the executable
override under the hood — though here we instead poke the manager
directly when we need to verify state, since POST always goes through
the inventory lookup path which doesn't expose that override.

The default fixture wires a bypass-mode authenticator (single user
``dev@local``) so we don't have to mint sessions; another fixture
swaps in a non-bypass authenticator to assert the 401 gate.
"""
from __future__ import annotations

import json
import textwrap
import urllib.error
import urllib.request
from typing import Any, Optional, Sequence

import pytest
from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    InMemoryInventoryStore,
    ManualRouter,
    Orchestrator,
    QueueApprovalHook,
    TaskMessage,
)
from dashboard.auth import AuthConfig, Authenticator
from dashboard.server import DashboardServer
from dashboard.terminal import SessionManager


_FAKE_KEY = textwrap.dedent(
    """
    -----BEGIN OPENSSH PRIVATE KEY-----
    AAAAB3NzaC1yc2EAAAADAQABAAABAQDterm
    -----END OPENSSH PRIVATE KEY-----
    """
).strip()


class _Stub(AgentSpec):
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()
    name = "stub"
    domain = "stub"

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        return AgentResult(task_id=task.task_id, status="success",
                           summary="ok", artifacts={}, cost=CostBreakdown())


@pytest.fixture
def inventory():
    s = InMemoryInventoryStore()
    k = s.add_key(name="cluster", content=_FAKE_KEY)
    s.add_host(name="cp", address="10.0.0.1", ssh_user="ubuntu",
               key_id=k.id, groups=["control_plane"])
    s.add_host(name="w1", address="10.0.0.2", ssh_user="root",
               ssh_port=2222, key_id=k.id)
    s.add_host(name="nokey", address="10.0.0.3", ssh_user="ubuntu")
    return s


def _build_server(*, auth: Authenticator, inventory_store,
                  session_manager: Optional[SessionManager] = None) -> DashboardServer:
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(bus=bus, agents=[_Stub()], ctx=ctx,
                        router=ManualRouter(default="stub"),
                        result_timeout_seconds=5.0)
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, auth=auth,
        inventory_store=inventory_store,
        session_manager=session_manager,
    )
    srv.serve()
    return srv


@pytest.fixture
def server(inventory):
    # Use /bin/cat under the hood so POST doesn't actually try to ssh.
    # We monkey-patch the manager's create to inject the override.
    real_mgr = SessionManager()
    real_create = real_mgr.create

    def fake_create(**kwargs):
        # Force benign subprocess regardless of what the REST layer asked.
        kwargs["executable"] = "/bin/cat"
        kwargs["args"] = []
        # Drop key_content so we don't write tempfiles we forget to clean.
        kwargs.pop("key_content", None)
        return real_create(**kwargs)
    real_mgr.create = fake_create  # type: ignore[method-assign]

    srv = _build_server(
        auth=Authenticator(AuthConfig(bypass=True)),
        inventory_store=inventory,
        session_manager=real_mgr,
    )
    yield srv
    srv.shutdown()


@pytest.fixture
def auth_required_server(inventory):
    srv = _build_server(
        auth=Authenticator(AuthConfig(
            bypass=False, session_secret=b"test", allowed_domains=frozenset(),
        )),
        inventory_store=inventory,
    )
    yield srv
    srv.shutdown()


def _request(server, method: str, path: str, body: Optional[dict] = None,
             timeout: float = 2.0) -> tuple[int, Any]:
    host, port = server.address
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"http://{host}:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_terminal_endpoints_require_auth(auth_required_server):
    for method, path in [
        ("GET", "/terminal/sessions"),
        ("POST", "/terminal/sessions"),
        ("DELETE", "/terminal/sessions/abc"),
    ]:
        body = {} if method == "POST" else None
        status, _ = _request(auth_required_server, method, path, body)
        assert status == 401, f"{method} {path} should require auth (got {status})"


# ---------------------------------------------------------------------------
# Create + list + delete round-trip
# ---------------------------------------------------------------------------


def test_create_session_returns_wire_shape_with_ws_url(server):
    status, payload = _request(server, "POST", "/terminal/sessions",
                               {"host_alias": "cp"})
    assert status == 201, payload
    s = payload["session"]
    # Wire-friendly fields are present.
    for key in ("session_id", "host_alias", "ssh_user", "address",
                "created_at", "attached", "last_active_at", "alive", "ws_url"):
        assert key in s, f"missing wire field: {key}"
    assert s["host_alias"] == "cp"
    assert s["ssh_user"] == "ubuntu"   # from the inventory
    assert s["alive"] is True
    assert s["attached"] is False
    # WS URL points back at this session — TERM.2b will service it.
    assert s["ws_url"] == f"/terminal/sessions/{s['session_id']}/ws"


def test_create_with_ssh_user_override(server):
    """The frontend's identity selector overrides the host's default
    ssh_user on a per-session basis without mutating the inventory."""
    status, payload = _request(server, "POST", "/terminal/sessions",
                               {"host_alias": "cp", "ssh_user": "alice"})
    assert status == 201
    assert payload["session"]["ssh_user"] == "alice"


def test_create_with_unknown_host_404(server):
    status, payload = _request(server, "POST", "/terminal/sessions",
                               {"host_alias": "does-not-exist"})
    assert status == 404
    assert "unknown host alias" in payload["error"]


def test_create_with_missing_host_alias_400(server):
    status, payload = _request(server, "POST", "/terminal/sessions",
                               {"ssh_user": "ubuntu"})
    assert status == 400
    assert "host_alias" in payload["error"]


def test_create_with_invalid_json_400(server):
    host, port = server.address
    req = urllib.request.Request(
        f"http://{host}:{port}/terminal/sessions",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2.0)
        assert False, "expected HTTPError"
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_list_sessions_shows_only_own_sessions(server):
    # Create one as the bypass-default user (dev@local).
    _request(server, "POST", "/terminal/sessions", {"host_alias": "cp"})
    # Plant a session owned by ANOTHER user directly via the manager.
    server.session_manager.create(
        owner_email="someone-else@x", host_alias="w1", ssh_user="root",
        address="10.0.0.2",
    )
    status, payload = _request(server, "GET", "/terminal/sessions")
    assert status == 200
    aliases = [s["host_alias"] for s in payload["sessions"]]
    assert "cp" in aliases
    assert "w1" not in aliases  # Other user's session not surfaced.


def test_delete_session_succeeds_for_owner(server):
    _, created = _request(server, "POST", "/terminal/sessions",
                          {"host_alias": "cp"})
    sid = created["session"]["session_id"]
    status, _ = _request(server, "DELETE", f"/terminal/sessions/{sid}")
    assert status == 200
    assert server.session_manager.get(sid) is None


def test_delete_session_unknown_id_404(server):
    status, _ = _request(server, "DELETE", "/terminal/sessions/does-not-exist")
    assert status == 404


def test_delete_other_users_session_returns_404_not_403(server):
    """Don't leak existence to non-owners — mimic 404 (vs 403) so an
    attacker can't probe session ids."""
    foreign = server.session_manager.create(
        owner_email="foreign@x", host_alias="w1", ssh_user="root",
        address="10.0.0.2",
    )
    status, _ = _request(server, "DELETE",
                         f"/terminal/sessions/{foreign.session_id}")
    assert status == 404
    # And it's still alive — we didn't actually close it.
    assert server.session_manager.get(foreign.session_id) is not None


def test_create_with_no_key_host_still_works(server):
    """Hosts in the inventory with no SSH key (rely on agent / default
    identity) should still produce a session — POST just doesn't
    materialize a key file."""
    status, payload = _request(server, "POST", "/terminal/sessions",
                               {"host_alias": "nokey"})
    assert status == 201
    assert payload["session"]["host_alias"] == "nokey"


def test_ask_endpoint_returns_companion_answer(server):
    """TERM.4b — POST /terminal/sessions/{id}/ask runs the
    terminal_companion in single-shot mode against the session's
    scrollback and surfaces a structured answer."""
    # Create a session first.
    _, created = _request(server, "POST", "/terminal/sessions",
                          {"host_alias": "cp"})
    sid = created["session"]["session_id"]

    # Mock the companion's ask() to skip the real LLM call.
    from terminal_companion.agent import TerminalCompanionResponse
    fake_resp = TerminalCompanionResponse(
        answer="the working directory looks like /opt/olympus",
        suggested_commands=["pwd", "ls -la"],
    )
    with __import__("unittest.mock").mock.patch(
        "terminal_companion.ask", return_value=fake_resp,
    ):
        status, payload = _request(
            server, "POST", f"/terminal/sessions/{sid}/ask",
            {"question": "where are we?"},
        )
    assert status == 200
    assert payload["answer"] == "the working directory looks like /opt/olympus"
    assert payload["suggested_commands"] == ["pwd", "ls -la"]


def test_ask_endpoint_404_for_foreign_session(server):
    """Non-owners get 404 (not 403) so session ids stay un-probable.
    Same posture as DELETE."""
    foreign = server.session_manager.create(
        owner_email="foreign@x", host_alias="w1", ssh_user="root",
        address="10.0.0.2",
    )
    status, payload = _request(
        server, "POST", f"/terminal/sessions/{foreign.session_id}/ask",
        {"question": "anything"},
    )
    assert status == 404


def test_ask_endpoint_400_when_question_missing(server):
    _, created = _request(server, "POST", "/terminal/sessions",
                          {"host_alias": "cp"})
    sid = created["session"]["session_id"]
    status, payload = _request(
        server, "POST", f"/terminal/sessions/{sid}/ask",
        {"question": "   "},
    )
    assert status == 400
    assert "question" in payload["error"]


def test_ask_endpoint_requires_auth(auth_required_server):
    """Auth gate covers /terminal/ — POST .../ask is included."""
    status, _ = _request(
        auth_required_server, "POST", "/terminal/sessions/sid/ask",
        {"question": "hi"},
    )
    assert status == 401


def test_dashboard_shutdown_reaps_terminal_sessions(inventory):
    """Closing the dashboard should SIGHUP every live pty so we don't
    leave orphan children when the pod restarts."""
    real_mgr = SessionManager()
    real_create = real_mgr.create
    real_mgr.create = lambda **kw: real_create(  # type: ignore[method-assign]
        **{**kw, "executable": "/bin/cat", "args": [], "key_content": None},
    )

    srv = _build_server(
        auth=Authenticator(AuthConfig(bypass=True)),
        inventory_store=inventory,
        session_manager=real_mgr,
    )
    try:
        _request(srv, "POST", "/terminal/sessions", {"host_alias": "cp"})
        assert len(real_mgr.list_for_user("dev@local")) == 1
    finally:
        srv.shutdown()
    assert real_mgr.list_for_user("dev@local") == []
