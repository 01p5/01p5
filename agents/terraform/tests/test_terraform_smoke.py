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
    # tf_apply + tf_destroy are user-facing mutations; tf_restore_state
    # is the rollback inverse for tf_apply (state push + reconcile) and
    # is destructive in its own right.
    assert spec.destructive_verbs == {"tf_apply", "tf_destroy", "tf_restore_state"}
    declared = {t.name for t in spec.tools}
    for required in ("tf_init", "tf_plan", "tf_apply", "tf_destroy", "tf_restore_state"):
        assert required in declared
    # tf_apply has a registered rollback snapshot.
    assert "tf_apply" in spec.rollback_snapshots


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


# ---------------------------------------------------------------------
# tf_restore_state + tf_apply rollback snapshot
# ---------------------------------------------------------------------

from agentlib import InMemoryRollbackStore as _InMemRb  # noqa: E402
from terraform.tools import _snapshot_tf_apply  # noqa: E402


def test_tf_restore_state_blocked_when_human_rejects(tmp_path):
    spec = TerraformAgent()
    ctx, _ = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="rs-1")
    by_name = {t.name: t for t in gated}

    with patch("terraform.tools.subprocess.run") as run_mock:
        out = by_name["tf_restore_state"].invoke({
            "working_dir": str(tmp_path),
            "state_json": '{"version":4,"terraform_version":"1.5.0"}',
        })

    assert "REJECTED" in out
    run_mock.assert_not_called()


def test_tf_restore_state_rejects_empty_state_without_running_terraform(tmp_path):
    spec = TerraformAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="rs-2")
    by_name = {t.name: t for t in gated}

    with patch("terraform.tools.subprocess.run") as run_mock:
        out = by_name["tf_restore_state"].invoke({
            "working_dir": str(tmp_path),
            "state_json": "   ",
        })

    assert "empty state" in out.lower()
    run_mock.assert_not_called()


def test_tf_restore_state_rejects_missing_working_dir(tmp_path):
    spec = TerraformAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="rs-3")
    by_name = {t.name: t for t in gated}

    bogus = str(tmp_path / "does-not-exist")
    with patch("terraform.tools.subprocess.run") as run_mock:
        out = by_name["tf_restore_state"].invoke({
            "working_dir": bogus,
            "state_json": '{"version":4}',
        })

    assert "does not exist" in out
    run_mock.assert_not_called()


def test_tf_restore_state_pipes_state_via_stdin_then_runs_apply(tmp_path):
    """Successful rollback: state push gets the JSON via stdin, then
    apply runs against the same dir. Both calls observable on the
    mocked subprocess.run."""
    spec = TerraformAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="rs-4")
    by_name = {t.name: t for t in gated}

    state_json = '{"version":4,"terraform_version":"1.5.0","resources":[]}'
    call_records: list[dict] = []

    def fake_run(cmd, **kwargs):
        call_records.append({"cmd": cmd, "input": kwargs.get("input"), "cwd": kwargs.get("cwd")})
        if cmd[:3] == ["terraform", "state", "push"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:2] == ["terraform", "apply"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Apply complete!\n", stderr="")
        raise AssertionError(f"unexpected: {cmd}")

    with patch("terraform.tools.subprocess.run", side_effect=fake_run):
        out = by_name["tf_restore_state"].invoke({
            "working_dir": str(tmp_path),
            "state_json": state_json,
        })

    # Both calls happened, in order.
    assert call_records[0]["cmd"] == ["terraform", "state", "push", "-"]
    assert call_records[0]["input"] == state_json
    assert call_records[0]["cwd"] == str(tmp_path)
    assert call_records[1]["cmd"][:2] == ["terraform", "apply"]
    assert "STATE PUSH OK" in out
    assert "Apply complete" in out


def test_tf_restore_state_state_push_failure_aborts_before_apply(tmp_path):
    """If the state push fails, the apply must NOT fire — the user
    needs to recover manually rather than have the rollback double
    down on the broken state."""
    spec = TerraformAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="rs-5")
    by_name = {t.name: t for t in gated}

    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen_cmds.append(cmd)
        if cmd[:3] == ["terraform", "state", "push"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="",
                stderr="error: state version mismatch",
            )
        # Should not be reached.
        raise AssertionError(f"apply ran despite state push failure: {cmd}")

    with patch("terraform.tools.subprocess.run", side_effect=fake_run):
        out = by_name["tf_restore_state"].invoke({
            "working_dir": str(tmp_path),
            "state_json": '{"version":4}',
        })

    assert "STATE PUSH FAILED" in out
    assert "version mismatch" in out
    assert "NOT touched" in out
    assert len(seen_cmds) == 1  # only the push; apply never ran


def test_tf_apply_snapshot_captures_state_via_pull(tmp_path):
    """The snapshot fn must invoke `terraform state pull` in the
    target working_dir and stash the resulting JSON as inverse args."""
    fake_state = '{"version":4,"resources":[{"name":"x","type":"local_file"}]}'

    with patch("terraform.tools.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=fake_state, stderr="",
        )
        plan = _snapshot_tf_apply({"working_dir": str(tmp_path)})

    args, kwargs = run_mock.call_args
    assert args[0] == ["terraform", "state", "pull"]
    assert kwargs["cwd"] == str(tmp_path)

    assert plan.inverse_tool == "tf_restore_state"
    assert plan.inverse_args["working_dir"] == str(tmp_path)
    assert plan.inverse_args["state_json"] == fake_state
    assert plan.snapshot["captured"] is True
    assert plan.snapshot["bytes"] == len(fake_state)


def test_tf_apply_snapshot_state_pull_failure_returns_noop_plan(tmp_path):
    """When no prior state exists (first apply) or terraform errors,
    the snapshot fn must still return a plan — flagged as no-op so
    the rollback panel shows the entry as non-executable."""
    with patch("terraform.tools.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="No state file was found!",
        )
        plan = _snapshot_tf_apply({"working_dir": str(tmp_path)})

    assert plan.snapshot["captured"] is False
    assert "NO-OP" in plan.description
    assert plan.inverse_args["state_json"] == ""


def test_tf_apply_snapshot_empty_state_pull_returns_noop_plan(tmp_path):
    """terraform state pull can succeed with empty output if there's
    nothing to dump. Treat that as no-op rather than persisting an
    empty rollback entry."""
    with patch("terraform.tools.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="   \n", stderr="",
        )
        plan = _snapshot_tf_apply({"working_dir": str(tmp_path)})

    assert plan.snapshot["captured"] is False


def test_tf_apply_round_trip_with_rollback_capture(tmp_path):
    """End-to-end: gate_tools wraps tf_apply, approval granted, the
    snapshot fn captures state, the rollback entry lands with the
    right inverse args."""
    fake_state = '{"version":4,"resources":[]}'
    spec = TerraformAgent()
    store = _InMemRb()
    ctx = AgentContext(
        approval=AlwaysApprove(),
        audit=InMemoryAuditLogger(),
        rollback=store,
    )
    gated = gate_tools(spec, ctx, task_id="rb-tf-1")
    by_name = {t.name: t for t in gated}

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["terraform", "state", "pull"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=fake_state, stderr="")
        if cmd[:2] == ["terraform", "apply"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Apply complete!\n", stderr="")
        raise AssertionError(f"unexpected: {cmd}")

    with patch("terraform.tools.subprocess.run", side_effect=fake_run):
        out = by_name["tf_apply"].invoke({"working_dir": str(tmp_path)})

    assert "Apply complete" in out

    entries = store.list_for_task("rb-tf-1")
    assert len(entries) == 1
    e = entries[0]
    assert e.forward_tool == "tf_apply"
    assert e.inverse_tool == "tf_restore_state"
    assert e.inverse_args["state_json"] == fake_state
    assert e.inverse_args["working_dir"] == str(tmp_path)
