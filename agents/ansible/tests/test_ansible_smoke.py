"""
Smoke tests for the Ansible agent.

Same shape as the Terraform / Sysadmin smoke tests. Subprocess-mocked
so they run without ansible-playbook + a target inventory.
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
    gate_tools,
)
from ansible_agent.agent import AnsibleAgent


def _ctx(approval=None):
    audit = InMemoryAuditLogger()
    return AgentContext(approval=approval or AlwaysApprove(), audit=audit), audit


def test_ansible_declares_destructive_verbs_correctly():
    spec = AnsibleAgent()
    assert spec.destructive_verbs == {"run_playbook", "run_module"}
    declared = {t.name for t in spec.tools}
    for required in ("list_inventory", "check_playbook", "run_playbook"):
        assert required in declared


def test_check_playbook_runs_without_approval():
    spec = AnsibleAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="ans-smoke-1")
    by_name = {t.name: t for t in gated}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="changed=0 ok=3\n", stderr="")

    with patch("ansible_agent.tools.subprocess.run", side_effect=fake_run):
        out = by_name["check_playbook"].invoke(
            {"playbook": "site.yml", "inventory": "hosts.ini"}
        )

    assert "ok=3" in out
    assert audit.records[0]["approved"] is None


def test_run_playbook_blocked_when_human_rejects():
    spec = AnsibleAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="ans-smoke-2")
    by_name = {t.name: t for t in gated}

    with patch("ansible_agent.tools.subprocess.run") as run_mock:
        result = by_name["run_playbook"].invoke(
            {"playbook": "site.yml", "inventory": "hosts.ini"}
        )

    assert "REJECTED" in result
    run_mock.assert_not_called()
    assert audit.records[0]["approved"] is False


def test_run_module_runs_after_approval():
    spec = AnsibleAgent()
    ctx, audit = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="ans-smoke-3")
    by_name = {t.name: t for t in gated}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="host1 | SUCCESS", stderr="")

    with patch("ansible_agent.tools.subprocess.run", side_effect=fake_run):
        out = by_name["run_module"].invoke(
            {"inventory": "hosts.ini", "pattern": "all", "module": "ping"}
        )

    assert "SUCCESS" in out
    tools = [r["tool"] for r in audit.records]
    assert tools == ["run_module", "run_module"]


def test_run_playbook_approval_call_carries_no_diff_preview():
    """run_playbook is destructive but not file-preview-able; the
    approval card must receive diff=None, not a stringified args dump."""
    spec = AnsibleAgent()
    seen: dict = {}

    class _Snoop:
        def request(self, **kw):
            from agentlib import ApprovalDecision
            seen.update(kw)
            return ApprovalDecision(approved=False, reason="snoop")

    audit = InMemoryAuditLogger()
    ctx = AgentContext(approval=_Snoop(), audit=audit)
    gated = gate_tools(spec, ctx, task_id="ans-diff-none-1")
    by_name = {t.name: t for t in gated}

    with patch("ansible_agent.tools.subprocess.run"):
        by_name["run_playbook"].invoke({"playbook": "site.yml", "inventory": "hosts.ini"})

    assert "diff" in seen
    assert seen["diff"] is None


_LIVE_REQUIRED = ("OLYMPUS_LIVE_LLM", "OLYMPUS_LIVE_ANSIBLE")


@pytest.mark.skipif(
    not all(os.environ.get(v) == "1" for v in _LIVE_REQUIRED),
    reason=f"set {' and '.join(_LIVE_REQUIRED)}=1 to run end-to-end",
)
def test_ansible_live_handles_real_task():
    """End-to-end against a real inventory + LLM. Opt-in."""
    from agentlib import ConsoleApprovalHook, JsonlAuditLogger, TaskMessage

    ctx = AgentContext(
        approval=ConsoleApprovalHook(),
        audit=JsonlAuditLogger("/tmp/olympus-live-audit.jsonl"),
    )
    task = TaskMessage(
        task_id="ans-live-1",
        natural_language=os.environ.get(
            "OLYMPUS_LIVE_ANSIBLE_TASK",
            "List the hosts in inventory hosts.ini",
        ),
    )
    result = AnsibleAgent().handle(task, ctx)
    assert result.status == "success"
