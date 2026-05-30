"""
Tests for the ssh_run tool factory + SysadminAgent integration.

The actual ssh subprocess is mocked — we verify the tool's plumbing:
host alias resolution, key materialization (0600 + cleanup), command
quoting, error paths, and the agent's per-handle injection through
gate_tools(extra_tools=[...]).
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from agentlib import (
    AgentContext,
    AlwaysApprove,
    InMemoryAuditLogger,
    InMemoryInventoryStore,
    TaskMessage,
)
from sysadmin.agent import SysadminAgent, SysadminResponse
from sysadmin.tools import make_ssh_run_tool


FAKE_KEY = textwrap.dedent(
    """
    -----BEGIN OPENSSH PRIVATE KEY-----
    AAAAB3NzaC1yc2EAAAADAQABAAABAQDsshrun
    -----END OPENSSH PRIVATE KEY-----
    """
).strip()


@pytest.fixture
def store():
    s = InMemoryInventoryStore()
    k = s.add_key(name="prod", content=FAKE_KEY)
    s.add_host(name="cp", address="10.0.0.1", ssh_user="ubuntu",
               key_id=k.id, groups=["control_plane"])
    s.add_host(name="w1", address="10.0.0.2", ssh_user="root",
               ssh_port=2222, key_id=k.id)
    s.add_host(name="nokey", address="10.0.0.3", ssh_user="ubuntu")
    return s


# ---------------------------------------------------------------------
# ssh_run direct unit tests
# ---------------------------------------------------------------------


def test_unknown_host_returns_error(store):
    tool = make_ssh_run_tool(store)
    out = tool.invoke({"host_alias": "does-not-exist", "command": "ls"})
    assert "unknown host alias" in out


def test_empty_command_returns_error(store):
    tool = make_ssh_run_tool(store)
    out = tool.invoke({"host_alias": "cp", "command": "   "})
    assert "empty command" in out


def test_ssh_invocation_uses_resolved_address_and_user(store):
    tool = make_ssh_run_tool(store)
    fake = MagicMock(returncode=0, stdout="hello\n", stderr="")
    with patch("sysadmin.tools.subprocess.run", return_value=fake) as run:
        out = tool.invoke({"host_alias": "cp", "command": "echo hello"})
    args = run.call_args.args[0]
    # First token = ssh; target appears as user@addr; command tail is preserved.
    assert args[0] == "ssh"
    assert "ubuntu@10.0.0.1" in args
    assert "echo hello" in args
    # Default port 22 → no -p flag.
    assert "-p" not in args
    assert "EXIT=0" in out
    assert "hello" in out


def test_ssh_invocation_includes_port_when_non_default(store):
    tool = make_ssh_run_tool(store)
    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("sysadmin.tools.subprocess.run", return_value=fake) as run:
        tool.invoke({"host_alias": "w1", "command": "uptime"})
    args = run.call_args.args[0]
    assert "-p" in args
    assert args[args.index("-p") + 1] == "2222"
    assert "root@10.0.0.2" in args


def test_no_key_still_runs_without_i_flag(store):
    tool = make_ssh_run_tool(store)
    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("sysadmin.tools.subprocess.run", return_value=fake) as run:
        tool.invoke({"host_alias": "nokey", "command": "true"})
    args = run.call_args.args[0]
    assert "-i" not in args


def test_key_is_materialized_to_0600_and_cleaned_up(store, tmp_path):
    # Capture the temp-key path the tool uses by intercepting subprocess.run
    # mid-flight and reading the file before we return.
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        idx = cmd.index("-i") + 1
        key_path = cmd[idx]
        st = os.stat(key_path)
        seen["mode"] = st.st_mode & 0o777
        seen["content"] = open(key_path, "r", encoding="utf-8").read()
        seen["key_path"] = key_path
        return MagicMock(returncode=0, stdout="", stderr="")

    tool = make_ssh_run_tool(store)
    with patch("sysadmin.tools.subprocess.run", side_effect=fake_run):
        tool.invoke({"host_alias": "cp", "command": "id"})
    assert seen["mode"] == 0o600
    assert FAKE_KEY.strip() in seen["content"]
    # And it's removed after the call returns.
    assert not os.path.exists(seen["key_path"])


def test_returns_stderr_in_output(store):
    tool = make_ssh_run_tool(store)
    fake = MagicMock(returncode=1, stdout="out", stderr="permission denied")
    with patch("sysadmin.tools.subprocess.run", return_value=fake):
        out = tool.invoke({"host_alias": "cp", "command": "ls /root"})
    assert "EXIT=1" in out
    assert "permission denied" in out


def test_timeout_returns_friendly_error(store):
    tool = make_ssh_run_tool(store)
    with patch("sysadmin.tools.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=1)):
        out = tool.invoke({"host_alias": "cp", "command": "sleep 5",
                           "timeout_sec": 1})
    assert "timeout" in out.lower()


def test_missing_key_content_returns_error(store):
    # Simulate a host that references a deleted/missing key.
    # We can't delete a referenced key through the store, but we can
    # mutate the internal map for this edge case.
    store._key_contents.clear()  # noqa: SLF001 — deliberate test fixture
    tool = make_ssh_run_tool(store)
    out = tool.invoke({"host_alias": "cp", "command": "id"})
    assert "missing" in out


# ---------------------------------------------------------------------
# SysadminAgent.handle injects ssh_run when inventory_store is wired
# ---------------------------------------------------------------------


def test_ssh_run_in_destructive_verbs_class_attribute():
    assert "ssh_run" in SysadminAgent.destructive_verbs


def test_handle_injects_ssh_run_when_inventory_present(store):
    spec = SysadminAgent()
    ctx = AgentContext(
        approval=AlwaysApprove(),
        audit=InMemoryAuditLogger(),
        inventory_store=store,
    )
    captured: dict[str, list] = {}

    def fake_structural(*, tools, **kwargs):
        captured["tools"] = list(tools)
        m = MagicMock()
        m.invoke.return_value = SysadminResponse(summary="ok")
        return m

    with patch("sysadmin.agent.StructuralAgent", side_effect=fake_structural), \
         patch("sysadmin.agent.cost_from_agent", return_value=None):
        spec.handle(TaskMessage(task_id="t", natural_language="hi"), ctx)

    names = [t.name for t in captured["tools"]]
    assert "ssh_run" in names


def test_handle_omits_ssh_run_when_no_inventory():
    spec = SysadminAgent()
    ctx = AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())
    captured: dict[str, list] = {}

    def fake_structural(*, tools, **kwargs):
        captured["tools"] = list(tools)
        m = MagicMock()
        m.invoke.return_value = SysadminResponse(summary="ok")
        return m

    with patch("sysadmin.agent.StructuralAgent", side_effect=fake_structural), \
         patch("sysadmin.agent.cost_from_agent", return_value=None):
        spec.handle(TaskMessage(task_id="t", natural_language="hi"), ctx)

    names = [t.name for t in captured["tools"]]
    assert "ssh_run" not in names
