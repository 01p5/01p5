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
from dashboard.server import DashboardServer
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


def _seed_settled(server, task_id, owner, cost, *, it=0, ot=0, ws=0.0, when=None):
    """Write a settled task straight into the durable ledger — the
    accounting endpoints read from there, not self._tasks."""
    when = time.time() if when is None else when
    server.accounting.record_submitted(
        task_id, owner_email=owner, natural_language="x", submitted_at=when)
    server.accounting.record_result(
        task_id, status="success", cost_usd=cost, input_tokens=it,
        output_tokens=ot, wall_seconds=ws, agent="stub")


def test_accounting_rolls_up_per_user(admin_server):
    _seed_settled(admin_server, "t1", "alice@x.com", 2.0, it=100, ot=50, ws=1.0)
    _seed_settled(admin_server, "t2", "alice@x.com", 3.0, it=200, ot=80, ws=2.0)
    _seed_settled(admin_server, "t3", "bob@x.com", 1.0)

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
    admin_server.accounting.record_submitted(
        "t1", owner_email="alice@x.com", natural_language="deploy the thing",
        submitted_at=time.time(), agent="sysadmin")
    admin_server.accounting.record_result(
        "t1", status="success", cost_usd=0.5, input_tokens=0, output_tokens=0,
        wall_seconds=0.0, agent="sysadmin")
    _, body = _req(admin_server, "GET", "/admin/activity")
    assert body["activity"][0]["owner_email"] == "alice@x.com"
    assert body["activity"][0]["task_id"] == "t1"


# ---------------------------------------------------------------------------
# Daily-limit enforcement on POST /tasks
# ---------------------------------------------------------------------------


def test_post_task_blocked_when_over_limit(admin_server):
    # Admin's own email is root@tianleyu.com (bypass dev_email). Set a
    # tiny cap + pre-seed a settled task that already exceeds it.
    admin_server.user_limit_store.set("root@tianleyu.com", 1.0)
    _seed_settled(admin_server, "seed", "root@tianleyu.com", 1.5)
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
# SQLite ledger: persistence + default limit
# ---------------------------------------------------------------------------


def test_accounting_store_persists_across_instances(tmp_path):
    from dashboard.accounting import SqliteAccountingStore
    db = tmp_path / "accounting.db"
    s1 = SqliteAccountingStore(db)
    s1.record_submitted("t1", owner_email="alice@x.com", natural_language="x",
                         submitted_at=time.time())
    s1.record_result("t1", status="success", cost_usd=4.0, input_tokens=10,
                     output_tokens=5, wall_seconds=1.0, agent="stub")
    s1.close()
    # A brand-new store over the same file still sees the row — this is
    # the whole point (survives a pod restart when the file's on a PVC).
    s2 = SqliteAccountingStore(db)
    rows = s2.per_user(0.0)
    assert len(rows) == 1
    assert rows[0]["email"] == "alice@x.com"
    assert rows[0]["usd"] == 4.0
    s2.close()


def test_default_limit_enforced_without_explicit_override():
    # Server with a $2 default; user has no explicit limit set.
    srv = _make_server("root@tianleyu.com", frozenset({"*@tianleyu.com"}))
    srv.default_user_daily_limit_usd = 2.0
    try:
        _seed_settled(srv, "seed", "root@tianleyu.com", 2.5)  # over the $2 default
        status, body = _req(srv, "POST", "/tasks", {"natural_language": "more"})
        assert status == 429
        assert body["daily_limit_usd"] == 2.0  # the default, not an explicit cap
    finally:
        srv.shutdown()


def test_explicit_limit_overrides_default():
    srv = _make_server("root@tianleyu.com", frozenset({"*@tianleyu.com"}))
    srv.default_user_daily_limit_usd = 2.0
    try:
        # Explicit higher cap should let a $2.50-spent user keep going.
        srv.user_limit_store.set("root@tianleyu.com", 100.0)
        _seed_settled(srv, "seed", "root@tianleyu.com", 2.5)
        status, _ = _req(srv, "POST", "/tasks", {"natural_language": "more"})
        assert status == 202
    finally:
        srv.shutdown()


def test_accounting_exposes_default_and_effective_limit(admin_server):
    admin_server.default_user_daily_limit_usd = 10.0
    _seed_settled(admin_server, "t1", "alice@x.com", 1.0)
    admin_server.user_limit_store.set("bob@x.com", 3.0)
    _, acct = _req(admin_server, "GET", "/admin/accounting")
    assert acct["default_daily_limit_usd"] == 10.0
    by_email = {u["email"]: u for u in acct["users"]}
    # alice: no explicit → effective is the default
    assert by_email["alice@x.com"]["daily_limit_usd"] is None
    assert by_email["alice@x.com"]["effective_limit_usd"] == 10.0
    # bob: explicit override wins
    assert by_email["bob@x.com"]["daily_limit_usd"] == 3.0
    assert by_email["bob@x.com"]["effective_limit_usd"] == 3.0


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
