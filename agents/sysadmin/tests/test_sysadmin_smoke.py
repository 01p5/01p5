"""
W1-2 PoC smoke test for the Sysadmin agent.

What this test proves end-to-end *without* an LLM or a real cluster:

  1. ``SysadminAgent`` declares its tools and destructive verbs correctly.
  2. ``gate_tools`` wraps every tool, the runtime executes read-only tools
     directly, and routes ``delete_pod`` through the approval hook.
  3. The audit log captures every call with the right ``approved`` value.

The actual LLM-driven path (``SysadminAgent.handle``) requires the
langchain>=1.0 stack and is exercised by ``test_sysadmin_live`` below
(skipped unless ``OLYMPUS_LIVE_LLM=1`` and a kubectl context is set).
"""
from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest
from agentlib import (
    AgentContext,
    AlwaysApprove,
    AlwaysReject,
    InMemoryAuditLogger,
    TaskMessage,
    gate_tools,
)
from sysadmin.agent import SysadminAgent


def _ctx(approval=None):
    audit = InMemoryAuditLogger()
    return (
        AgentContext(approval=approval or AlwaysApprove(), audit=audit),
        audit,
    )


def test_sysadmin_declares_destructive_verbs_correctly():
    spec = SysadminAgent()
    # delete_pod is the only mutation; nothing else should be in the set.
    assert spec.destructive_verbs == {"delete_pod"}
    assert "delete_pod" in {t.name for t in spec.tools}
    assert "get_pods" in {t.name for t in spec.tools}


def test_read_only_tool_runs_without_approval(monkeypatch):
    """get_pods is read-only — the runtime must not call the approval hook."""
    spec = SysadminAgent()
    # AlwaysReject would block any approval request — use it to prove
    # the read-only path bypasses approval entirely.
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="smoke-1")
    by_name = {t.name: t for t in gated}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="NAME READY\nweb 1/1\n", stderr="")

    with patch("sysadmin.tools.subprocess.run", side_effect=fake_run):
        out = by_name["get_pods"].invoke({"namespace": "default"})

    assert "web" in out
    assert len(audit.records) == 1
    assert audit.records[0]["tool"] == "get_pods"
    assert audit.records[0]["approved"] is None  # never asked


def test_destructive_tool_blocked_when_human_rejects():
    """delete_pod must NOT shell out when the human rejects."""
    spec = SysadminAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="smoke-2")
    by_name = {t.name: t for t in gated}

    with patch("sysadmin.tools.subprocess.run") as run_mock:
        result = by_name["delete_pod"].invoke({"name": "web-abc", "namespace": "default"})

    assert "REJECTED" in result
    run_mock.assert_not_called()
    assert len(audit.records) == 1
    assert audit.records[0]["approved"] is False


def test_destructive_tool_runs_after_approval(monkeypatch):
    """When approved, delete_pod shells out and the audit log records both
    the approval decision and the execution."""
    spec = SysadminAgent()
    ctx, audit = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="smoke-3")
    by_name = {t.name: t for t in gated}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout='pod "web-abc" deleted\n', stderr="")

    with patch("sysadmin.tools.subprocess.run", side_effect=fake_run):
        out = by_name["delete_pod"].invoke({"name": "web-abc", "namespace": "default"})

    assert "deleted" in out
    # Two audit records: pre-execution (approved=True, result=None) +
    # post-execution (approved=True, result=...).
    tools = [r["tool"] for r in audit.records]
    assert tools == ["delete_pod", "delete_pod"]
    assert audit.records[0]["result"] is None
    assert "deleted" in audit.records[1]["result"]


# ---------------------------------------------------------------------------
# Live test — only runs with explicit opt-in. CI must not pick this up.
# ---------------------------------------------------------------------------

_LIVE_REQUIRED = ("OLYMPUS_LIVE_LLM", "OLYMPUS_LIVE_KUBECTL")


@pytest.mark.skipif(
    not all(os.environ.get(v) == "1" for v in _LIVE_REQUIRED),
    reason=f"set {' and '.join(_LIVE_REQUIRED)}=1 to run the end-to-end PoC against a real cluster + LLM",
)
def test_sysadmin_live_handles_real_task():
    """End-to-end: NL request → LLM → kubectl → structured result.

    Requires:
      - kubectl configured against a cluster the user trusts (read-only path).
      - An LLM provider key (Anthropic) reachable by langchain.
      - ``langchain>=1.0`` and ``olympus_telemetry`` installed.
    """
    from agentlib import ConsoleApprovalHook, JsonlAuditLogger

    ctx = AgentContext(
        approval=ConsoleApprovalHook(),
        audit=JsonlAuditLogger("/tmp/olympus-live-audit.jsonl"),
    )
    task = TaskMessage(
        task_id="live-smoke-1",
        natural_language="List the pods in the default namespace and tell me if any are not running.",
    )
    result = SysadminAgent().handle(task, ctx)
    assert result.status == "success"
    assert result.summary  # non-empty
