"""
Smoke tests for the Terraform agent.

Mirror of tests/test_sysadmin_smoke.py. Proves the AgentSpec contract
holds for an agent whose destructive surface is "tf_apply, tf_destroy"
and whose read-only surface includes the plan that becomes the
approval-hook diff.

LLM- and cluster-free; ``terraform`` itself is mocked via subprocess.run.
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
from terraform.agent import TerraformAgent


def _ctx(approval=None):
    audit = InMemoryAuditLogger()
    return AgentContext(approval=approval or AlwaysApprove(), audit=audit), audit


def test_terraform_declares_destructive_verbs_correctly():
    spec = TerraformAgent()
    assert spec.destructive_verbs == {"tf_apply", "tf_destroy"}
    declared = {t.name for t in spec.tools}
    for required in ("tf_init", "tf_plan", "tf_apply", "tf_destroy"):
        assert required in declared


def test_plan_runs_without_approval(tmp_path):
    """tf_plan is read-only — never goes through ApprovalHook."""
    spec = TerraformAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="tf-smoke-1")
    by_name = {t.name: t for t in gated}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="Plan: 1 to add\n", stderr="")

    with patch("terraform.tools.subprocess.run", side_effect=fake_run):
        out = by_name["tf_plan"].invoke({"working_dir": str(tmp_path)})

    assert "Plan" in out
    assert audit.records[0]["approved"] is None


def test_apply_blocked_when_human_rejects(tmp_path):
    spec = TerraformAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="tf-smoke-2")
    by_name = {t.name: t for t in gated}

    with patch("terraform.tools.subprocess.run") as run_mock:
        result = by_name["tf_apply"].invoke({"working_dir": str(tmp_path)})

    assert "REJECTED" in result
    run_mock.assert_not_called()
    assert audit.records[0]["approved"] is False


def test_apply_runs_after_approval(tmp_path):
    spec = TerraformAgent()
    ctx, audit = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="tf-smoke-3")
    by_name = {t.name: t for t in gated}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="Apply complete!\n", stderr="")

    with patch("terraform.tools.subprocess.run", side_effect=fake_run):
        out = by_name["tf_apply"].invoke({"working_dir": str(tmp_path)})

    assert "Apply complete" in out
    tools = [r["tool"] for r in audit.records]
    assert tools == ["tf_apply", "tf_apply"]
    assert audit.records[1]["result"].startswith("Apply complete")


def test_missing_working_dir_returns_error_without_invoking_tf(tmp_path):
    """Defense in depth: tools.py validates cwd before shelling out."""
    spec = TerraformAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="tf-smoke-4")
    by_name = {t.name: t for t in gated}

    bad_dir = str(tmp_path / "does-not-exist")
    with patch("terraform.tools.subprocess.run") as run_mock:
        out = by_name["tf_plan"].invoke({"working_dir": bad_dir})

    assert "ERROR" in out and "does not exist" in out
    run_mock.assert_not_called()


def test_tf_apply_approval_call_carries_no_diff_preview(tmp_path):
    """tf_apply mutates infrastructure but not a single file the runtime
    can preview — so diff must be None at the approval card, not a
    stringified args dump."""
    spec = TerraformAgent()
    seen: dict = {}

    class _Snoop:
        def request(self, **kw):
            from agentlib import ApprovalDecision
            seen.update(kw)
            return ApprovalDecision(approved=False, reason="snoop")

    audit = InMemoryAuditLogger()
    ctx = AgentContext(approval=_Snoop(), audit=audit)
    gated = gate_tools(spec, ctx, task_id="tf-diff-none-1")
    by_name = {t.name: t for t in gated}

    with patch("terraform.tools.subprocess.run"):
        by_name["tf_apply"].invoke({"working_dir": str(tmp_path)})

    assert "diff" in seen
    assert seen["diff"] is None


_LIVE_REQUIRED = ("OLYMPUS_LIVE_LLM", "OLYMPUS_LIVE_TF")


@pytest.mark.skipif(
    not all(os.environ.get(v) == "1" for v in _LIVE_REQUIRED),
    reason=f"set {' and '.join(_LIVE_REQUIRED)}=1 to run end-to-end",
)
def test_terraform_live_handles_real_task():
    """End-to-end against a real terraform working dir + LLM. Opt-in."""
    from agentlib import ConsoleApprovalHook, JsonlAuditLogger, TaskMessage

    ctx = AgentContext(
        approval=ConsoleApprovalHook(),
        audit=JsonlAuditLogger("/tmp/olympus-live-audit.jsonl"),
    )
    task = TaskMessage(
        task_id="tf-live-1",
        natural_language=os.environ.get(
            "OLYMPUS_LIVE_TF_TASK",
            "Run terraform plan on the current directory and summarize the diff.",
        ),
    )
    result = TerraformAgent().handle(task, ctx)
    assert result.status == "success"
