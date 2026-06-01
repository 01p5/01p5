"""Super-admin accounting dashboard — backend tests (ADM.2/3).

Two layers:
  - UserLimitStore (in-memory + file-backed) as pure units.
  - The /admin/* endpoints + per-user task attribution + daily-limit
    enforcement, against a running DashboardServer.

The server fixtures use bypass auth with a chosen dev_email so we can
exercise both the admin (403-passing) and non-admin (403) paths
without standing up real OAuth.
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
    ManualRouter,
    Orchestrator,
    QueueApprovalHook,
    TaskMessage,
)
from dashboard.auth import AuthConfig, Authenticator
from dashboard.server import DashboardServer, TaskRecord
from dashboard.user_limits import FileBackedUserLimitStore, InMemoryUserLimitStore


# ---------------------------------------------------------------------------
# UserLimitStore unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("make_store", [
    lambda tmp: InMemoryUserLimitStore(),
    lambda tmp: FileBackedUserLimitStore(tmp / "user_limits.json"),
])
def test_user_limit_store_roundtrip(make_store, tmp_path):
    s = make_store(tmp_path)
    assert s.get("alice@x.com") is None
    s.set("alice@x.com", 10.0)
    assert s.get("alice@x.com") == 10.0
    assert s.get("ALICE@x.com") == 10.0  # case-insensitive
    assert s.all() == {"alice@x.com": 10.0}
    # Clear with None.
    s.set("alice@x.com", None)
    assert s.get("alice@x.com") is None
    assert s.all() == {}


@pytest.mark.parametrize("make_store", [
    lambda tmp: InMemoryUserLimitStore(),
    lambda tmp: FileBackedUserLimitStore(tmp / "user_limits.json"),
])
def test_user_limit_store_validation(make_store, tmp_path):
    s = make_store(tmp_path)
    with pytest.raises(ValueError):
        s.set("", 5.0)
    with pytest.raises(ValueError):
        s.set("a@b.com", -1.0)


def test_file_backed_store_persists_across_instances(tmp_path):
    p = tmp_path / "user_limits.json"
    FileBackedUserLimitStore(p).set("bob@x.com", 25.0)
    # A fresh instance reads the same file.
    assert FileBackedUserLimitStore(p).get("bob@x.com") == 25.0


# ---------------------------------------------------------------------------
# Server fixtures
# ---------------------------------------------------------------------------


class _FreeAgent(AgentSpec):
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()
    name = "stub"
    domain = "stub"

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        return AgentResult(task_id=task.task_id, status="success",
                           summary="ok", artifacts={}, cost=CostBreakdown())


def _make_server(dev_email: str, admin_emails: frozenset[str]):
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus, agents=[_FreeAgent()], ctx=ctx,
        router=ManualRouter(default="stub"), result_timeout_seconds=5.0,
    )
    auth = Authenticator(AuthConfig(
        bypass=True, dev_email=dev_email, admin_emails=admin_emails,
    ))
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, auth=auth,
        user_limit_store=InMemoryUserLimitStore(),
    )
    srv.serve()
    return srv


@pytest.fixture
def admin_server():
    srv = _make_server("root@tianleyu.com", frozenset({"*@tianleyu.com"}))
    yield srv
    srv.shutdown()


@pytest.fixture
def plain_server():
    # Authenticated (bypass) but NOT admin.
    srv = _make_server("user@gmail.com", frozenset({"*@tianleyu.com"}))
    yield srv
    srv.shutdown()


def _req(server, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
    host, port = server.address
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://{host}:{port}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


# ---------------------------------------------------------------------------
# Admin gating
# ---------------------------------------------------------------------------


def test_accounting_requires_admin(plain_server):
    status, body = _req(plain_server, "GET", "/admin/accounting")
    assert status == 403
    assert "super-admin" in body["error"]


def test_accounting_ok_for_admin(admin_server):
    status, body = _req(admin_server, "GET", "/admin/accounting")
    assert status == 200
    assert "users" in body and "day_start_utc" in body


def test_set_limit_requires_admin(plain_server):
    status, _ = _req(plain_server, "PUT", "/admin/limits/alice@x.com",
                     {"daily_limit_usd": 5.0})
    assert status == 403


# ---------------------------------------------------------------------------
# Accounting rollup + limit set/clear
# ---------------------------------------------------------------------------


def test_set_and_clear_limit_roundtrip(admin_server):
    status, body = _req(admin_server, "PUT", "/admin/limits/alice@x.com",
                        {"daily_limit_usd": 12.5})
    assert status == 200 and body["daily_limit_usd"] == 12.5
    assert admin_server.user_limit_store.get("alice@x.com") == 12.5

    # Now it shows in accounting even with no tasks yet.
    _, acct = _req(admin_server, "GET", "/admin/accounting")
    alice = next(u for u in acct["users"] if u["email"] == "alice@x.com")
    assert alice["daily_limit_usd"] == 12.5

    # Clear.
    status, body = _req(admin_server, "PUT", "/admin/limits/alice@x.com",
                        {"daily_limit_usd": None})
    assert status == 200 and body["daily_limit_usd"] is None
    assert admin_server.user_limit_store.get("alice@x.com") is None


def test_accounting_rolls_up_per_user(admin_server):
    # Inject settled task records for two users.
    now = time.time()
    with admin_server._tasks_lock:
        admin_server._tasks["t1"] = TaskRecord(
            task_id="t1", natural_language="x", submitted_at=now,
            status="success", cost_usd=2.0, input_tokens=100, output_tokens=50,
            wall_seconds=1.0, agent="stub", owner_email="alice@x.com")
        admin_server._tasks["t2"] = TaskRecord(
            task_id="t2", natural_language="y", submitted_at=now,
            status="success", cost_usd=3.0, input_tokens=200, output_tokens=80,
            wall_seconds=2.0, agent="stub", owner_email="alice@x.com")
        admin_server._tasks["t3"] = TaskRecord(
            task_id="t3", natural_language="z", submitted_at=now,
            status="success", cost_usd=1.0, agent="stub",
            owner_email="bob@x.com")

    _, acct = _req(admin_server, "GET", "/admin/accounting")
    by_email = {u["email"]: u for u in acct["users"]}
    assert by_email["alice@x.com"]["usd"] == 5.0
    assert by_email["alice@x.com"]["tasks"] == 2
    assert by_email["alice@x.com"]["input_tokens"] == 300
    assert by_email["alice@x.com"]["spent_today_usd"] == 5.0
    assert by_email["bob@x.com"]["usd"] == 1.0
    # Highest spender sorts first.
    assert acct["users"][0]["email"] == "alice@x.com"


def test_activity_feed_includes_owner(admin_server):
    now = time.time()
    with admin_server._tasks_lock:
        admin_server._tasks["t1"] = TaskRecord(
            task_id="t1", natural_language="deploy the thing",
            submitted_at=now, status="success", cost_usd=0.5,
            agent="sysadmin", owner_email="alice@x.com")
    _, body = _req(admin_server, "GET", "/admin/activity")
    assert body["activity"][0]["owner_email"] == "alice@x.com"
    assert body["activity"][0]["task_id"] == "t1"


# ---------------------------------------------------------------------------
# Daily-limit enforcement on POST /tasks
# ---------------------------------------------------------------------------


def test_post_task_blocked_when_over_limit(admin_server):
    # Admin's own email is root@tianleyu.com (bypass dev_email). Set a
    # tiny cap + pre-seed a task that already exceeds it, then submit.
    admin_server.user_limit_store.set("root@tianleyu.com", 1.0)
    with admin_server._tasks_lock:
        admin_server._tasks["seed"] = TaskRecord(
            task_id="seed", natural_language="prior", submitted_at=time.time(),
            status="success", cost_usd=1.5, owner_email="root@tianleyu.com")
    status, body = _req(admin_server, "POST", "/tasks",
                        {"natural_language": "do more"})
    assert status == 429
    assert "daily cost limit" in body["error"]
    assert body["daily_limit_usd"] == 1.0


def test_post_task_allowed_under_limit(admin_server):
    admin_server.user_limit_store.set("root@tianleyu.com", 100.0)
    status, body = _req(admin_server, "POST", "/tasks",
                        {"natural_language": "do a thing"})
    assert status == 202
    assert "task_id" in body


# ---------------------------------------------------------------------------
# Chat (ticket) activity is attributed to the user — the demo's real path
# ---------------------------------------------------------------------------


class _CostlyMain(AgentSpec):
    """A 'main' agent that returns a non-zero cost, so we can assert the
    chat turn lands in per-user accounting."""
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()
    name = "main"
    domain = "main"

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        return AgentResult(
            task_id=task.task_id, status="success", summary="done",
            artifacts={"resolved": True},
            cost=CostBreakdown(total_usd=0.42, input_tokens=120,
                               output_tokens=60, wall_seconds=1.5),
        )


def test_chat_turn_attributed_to_user_in_accounting():
    from agentlib import InMemoryTicketStore
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    store = InMemoryTicketStore()
    orch = Orchestrator(
        bus=bus, agents=[_CostlyMain()], ctx=ctx,
        router=ManualRouter(default="main"), ticket_store=store,
        result_timeout_seconds=5.0,
    )
    auth = Authenticator(AuthConfig(
        bypass=True, dev_email="root@tianleyu.com",
        admin_emails=frozenset({"*@tianleyu.com"}),
    ))
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, auth=auth, ticket_store=store,
        user_limit_store=InMemoryUserLimitStore(),
    )
    srv.serve()
    try:
        status, _ = _req(srv, "POST", "/tickets/t-demo/messages",
                         {"message": "scale the cluster"})
        assert status == 202
        # The worker runs async; poll the accounting until the cost lands.
        deadline = time.time() + 5
        alice = None
        while time.time() < deadline:
            _, acct = _req(srv, "GET", "/admin/accounting")
            users = {u["email"]: u for u in acct["users"]}
            if "root@tianleyu.com" in users and users["root@tianleyu.com"]["usd"] > 0:
                alice = users["root@tianleyu.com"]
                break
            time.sleep(0.1)
        assert alice is not None, "chat turn never attributed to user"
        assert alice["usd"] == pytest.approx(0.42)
        assert alice["input_tokens"] == 120
    finally:
        srv.shutdown()
