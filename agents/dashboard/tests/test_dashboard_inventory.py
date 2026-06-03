"""
Dashboard inventory endpoint tests (INV.2).

End-to-end against a running DashboardServer on a loopback port. The
store is in-memory; the auth gate is on by default in the test fixture
(so the unauthenticated branch is exercised), and we mint a bypass-mode
authenticator for the auth'd flow to keep tests focused on the inventory
behavior rather than the OAuth dance.
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


# Minimal valid-looking PEM body. The store's heuristic only checks for
# BEGIN/END PRIVATE KEY headers — no cryptographic validation.
FAKE_KEY = textwrap.dedent(
    """
    -----BEGIN OPENSSH PRIVATE KEY-----
    AAAAB3NzaC1yc2EAAAADAQABAAABAQDdashboard
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
def auth_bypass():
    return Authenticator(AuthConfig(bypass=True))


@pytest.fixture
def server(auth_bypass):
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus,
        agents=[_Stub()],
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
        auth=auth_bypass,
        inventory_store=InMemoryInventoryStore(),
    )
    srv.serve()
    yield srv
    srv.shutdown()


@pytest.fixture
def auth_required():
    """A real auth config — no bypass, no provider configured. Every
    gated route 401s, which is exactly what we want for the "unauth
    rejected" test below."""
    return Authenticator(AuthConfig(
        bypass=False,
        session_secret=b"unit-test-secret",
        allowed_domains=frozenset(),
    ))


@pytest.fixture
def auth_required_server(auth_required):
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(bus=bus, agents=[_Stub()], ctx=ctx,
                        router=ManualRouter(default="stub"),
                        result_timeout_seconds=5.0)
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0, auth=auth_required,
        inventory_store=InMemoryInventoryStore(),
    )
    srv.serve()
    yield srv
    srv.shutdown()


# ---------------------------------------------------------------------------
# Small HTTP helpers (matching test_dashboard_server.py style)
# ---------------------------------------------------------------------------


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
# Sync from terraform (operator action, admin-only)
# ---------------------------------------------------------------------------


def _admin_server():
    bus = InMemoryBus()
    approval = QueueApprovalHook(approval_timeout_seconds=5.0)
    ctx = AgentContext(approval=approval, audit=InMemoryAuditLogger())
    orch = Orchestrator(bus=bus, agents=[_Stub()], ctx=ctx,
                        router=ManualRouter(default="stub"), result_timeout_seconds=5.0)
    store = InMemoryInventoryStore()
    store.add_key(name="cluster", content=FAKE_KEY)
    srv = DashboardServer(
        orchestrator=orch, bus=bus, approval_hook=approval,
        host="127.0.0.1", port=0,
        auth=Authenticator(AuthConfig(bypass=True, admin_emails=frozenset({"*"}))),
        inventory_store=store,
    )
    srv.serve()
    return srv, store


def test_sync_terraform_inventory_seeds_hosts(monkeypatch, tmp_path):
    import subprocess

    srv, store = _admin_server()
    try:
        tf_out = json.dumps([
            {"name": "web1", "address": "10.0.0.5", "ssh_user": "ubuntu",
             "ssh_port": 22, "key": "cluster", "groups": ["web"],
             "vars": {"role": "frontend"}},
            {"name": "db1", "address": "10.0.0.6"},  # minimal, no key
        ])

        class _R:
            returncode = 0
            stdout = tf_out
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
        status, payload = _request(srv, "POST", "/inventory/sync-terraform",
                                   {"working_dir": str(tmp_path)})
        assert status == 200, payload
        assert set(payload["added"]) == {"web1", "db1"}
        names = {h.name for h in store.list_hosts()}
        assert {"web1", "db1"} <= names
        # idempotent: a second sync skips both
        _, payload2 = _request(srv, "POST", "/inventory/sync-terraform",
                               {"working_dir": str(tmp_path)})
        assert set(payload2["skipped"]) == {"web1", "db1"}
    finally:
        srv.shutdown()


def test_sync_terraform_inventory_requires_admin(server):
    # the `server` fixture is bypass but NOT admin (admin_emails empty) -> 403
    status, _ = _request(server, "POST", "/inventory/sync-terraform",
                         {"working_dir": "/tmp"})
    assert status == 403


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_inventory_endpoints_require_auth(auth_required_server):
    for method, path in [
        ("GET", "/inventory/hosts"),
        ("POST", "/inventory/hosts"),
        ("GET", "/inventory/keys"),
        ("POST", "/inventory/keys"),
        ("PUT", "/inventory/hosts/abc"),
        ("DELETE", "/inventory/hosts/abc"),
        ("DELETE", "/inventory/keys/abc"),
        ("GET", "/inventory/render"),
    ]:
        body = {} if method in ("POST", "PUT") else None
        status, payload = _request(auth_required_server, method, path, body)
        assert status == 401, f"{method} {path} should require auth, got {status}: {payload}"


# ---------------------------------------------------------------------------
# Hosts CRUD
# ---------------------------------------------------------------------------


def test_create_list_get_update_delete_host(server):
    # Empty list initially.
    status, payload = _request(server, "GET", "/inventory/hosts")
    assert status == 200
    assert payload == {"hosts": []}

    # Create.
    status, payload = _request(server, "POST", "/inventory/hosts", {
        "name": "cp",
        "address": "10.0.0.1",
        "ssh_user": "ubuntu",
        "groups": ["control_plane"],
        "vars": {"region": "us-west-2"},
    })
    assert status == 201, payload
    host = payload["host"]
    host_id = host["id"]
    assert host["name"] == "cp"
    assert host["groups"] == ["control_plane"]
    assert host["vars"] == {"region": "us-west-2"}

    # List shows it.
    status, payload = _request(server, "GET", "/inventory/hosts")
    assert status == 200
    assert len(payload["hosts"]) == 1
    assert payload["hosts"][0]["id"] == host_id

    # Update — partial.
    status, payload = _request(server, "PUT",
                               f"/inventory/hosts/{host_id}",
                               {"address": "10.0.0.99"})
    assert status == 200
    assert payload["host"]["address"] == "10.0.0.99"
    assert payload["host"]["name"] == "cp"  # untouched

    # Delete.
    status, payload = _request(server, "DELETE", f"/inventory/hosts/{host_id}")
    assert status == 200
    assert payload == {"ok": True}

    # Now 404 on second delete.
    status, payload = _request(server, "DELETE", f"/inventory/hosts/{host_id}")
    assert status == 404


def test_create_host_validation_errors(server):
    # Empty name → 400 with friendly message.
    status, payload = _request(server, "POST", "/inventory/hosts",
                               {"name": "", "address": "10.0.0.1"})
    assert status == 400
    assert "required" in payload["error"].lower()

    # Bad name (spaces) → 400.
    status, payload = _request(server, "POST", "/inventory/hosts",
                               {"name": "bad name", "address": "10.0.0.1"})
    assert status == 400

    # Reserved var → 400.
    status, payload = _request(server, "POST", "/inventory/hosts", {
        "name": "x", "address": "10.0.0.1",
        "vars": {"ansible_host": "10.0.0.99"},
    })
    assert status == 400
    assert "reserved" in payload["error"].lower()

    # Reserved group → 400.
    status, payload = _request(server, "POST", "/inventory/hosts", {
        "name": "y", "address": "10.0.0.1", "groups": ["all"],
    })
    assert status == 400


def test_create_host_duplicate_name(server):
    _request(server, "POST", "/inventory/hosts",
             {"name": "cp", "address": "10.0.0.1"})
    status, payload = _request(server, "POST", "/inventory/hosts",
                               {"name": "cp", "address": "10.0.0.2"})
    assert status == 400
    assert "already exists" in payload["error"]


def test_update_unknown_host_404(server):
    status, payload = _request(server, "PUT", "/inventory/hosts/nope",
                               {"address": "10.0.0.1"})
    assert status == 404


def test_post_invalid_json(server):
    host, port = server.address
    req = urllib.request.Request(
        f"http://{host}:{port}/inventory/hosts",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2.0)
        assert False, "expected HTTPError"
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


# ---------------------------------------------------------------------------
# Keys CRUD + content isolation
# ---------------------------------------------------------------------------


def test_create_list_delete_key(server):
    # Create.
    status, payload = _request(server, "POST", "/inventory/keys",
                               {"name": "prod", "content": FAKE_KEY})
    assert status == 201
    key = payload["key"]
    key_id = key["id"]
    assert key["fingerprint"].startswith("SHA256:")
    # Content MUST NOT be in the response payload.
    assert "content" not in key

    # List likewise omits content.
    status, payload = _request(server, "GET", "/inventory/keys")
    assert status == 200
    assert len(payload["keys"]) == 1
    assert "content" not in payload["keys"][0]
    assert payload["keys"][0]["fingerprint"] == key["fingerprint"]

    # Delete.
    status, payload = _request(server, "DELETE", f"/inventory/keys/{key_id}")
    assert status == 200
    assert payload == {"ok": True}


def test_delete_key_blocked_when_in_use(server):
    _, key_resp = _request(server, "POST", "/inventory/keys",
                           {"name": "k", "content": FAKE_KEY})
    key_id = key_resp["key"]["id"]
    _, host_resp = _request(server, "POST", "/inventory/hosts",
                            {"name": "h", "address": "10.0.0.1",
                             "key_id": key_id})
    status, payload = _request(server, "DELETE", f"/inventory/keys/{key_id}")
    assert status == 409
    assert "in use" in payload["error"].lower()


def test_create_key_validation(server):
    # Empty content → 400.
    status, payload = _request(server, "POST", "/inventory/keys",
                               {"name": "k", "content": ""})
    assert status == 400

    # Non-PEM-looking content → 400.
    status, payload = _request(server, "POST", "/inventory/keys",
                               {"name": "k", "content": "not a key"})
    assert status == 400
    assert "PRIVATE KEY" in payload["error"]

    # Bad name → 400.
    status, payload = _request(server, "POST", "/inventory/keys",
                               {"name": "", "content": FAKE_KEY})
    assert status == 400


def test_delete_unknown_key_404(server):
    status, payload = _request(server, "DELETE", "/inventory/keys/nope")
    assert status == 404


# ---------------------------------------------------------------------------
# Inventory render
# ---------------------------------------------------------------------------


def test_render_inventory_returns_ini(server):
    _request(server, "POST", "/inventory/hosts",
             {"name": "cp", "address": "10.0.0.1", "groups": ["control_plane"]})
    _request(server, "POST", "/inventory/hosts",
             {"name": "w1", "address": "10.0.0.2", "groups": ["workers"]})

    status, payload = _request(server, "GET", "/inventory/render")
    assert status == 200
    assert isinstance(payload, str)
    assert "[control_plane]" in payload
    assert "[workers]" in payload
    assert "cp ansible_host=10.0.0.1 ansible_user=ubuntu" in payload
    assert "w1 ansible_host=10.0.0.2 ansible_user=ubuntu" in payload
