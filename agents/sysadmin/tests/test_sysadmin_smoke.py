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
    # delete_pod is the user-facing mutation; apply_manifest is its
    # rollback inverse and is destructive too (a misused apply could
    # create or replace anything).
    assert spec.destructive_verbs == {"delete_pod", "apply_manifest"}
    declared = {t.name for t in spec.tools}
    assert "delete_pod" in declared
    assert "apply_manifest" in declared
    assert "get_pods" in declared
    # delete_pod has a registered rollback snapshot.
    assert "delete_pod" in spec.rollback_snapshots


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

def test_delete_pod_approval_call_carries_no_diff_preview():
    """Diff preview is opt-in by tool-name lookup in the runtime's
    _preview_diff (write_file/edit_file only). For non-file-mutating
    destructive verbs like delete_pod, the runtime must pass
    diff=None to ApprovalHook.request — not a stringified args dump."""
    spec = SysadminAgent()
    seen: dict = {}

    class _Snoop:
        def request(self, **kw):
            from agentlib import ApprovalDecision
            seen.update(kw)
            return ApprovalDecision(approved=False, reason="snoop")

    audit = InMemoryAuditLogger()
    ctx = AgentContext(approval=_Snoop(), audit=audit)
    gated = gate_tools(spec, ctx, task_id="diff-none-1")
    by_name = {t.name: t for t in gated}

    with patch("sysadmin.tools.subprocess.run"):
        by_name["delete_pod"].invoke({"name": "web-x", "namespace": "default"})

    assert "diff" in seen, "ApprovalHook.request must be called with a diff kwarg"
    assert seen["diff"] is None


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


# ---------------------------------------------------------------------
# apply_manifest + delete_pod rollback snapshot
# ---------------------------------------------------------------------

from agentlib import InMemoryRollbackStore as _InMemRb  # noqa: E402
from sysadmin.tools import _scrub_server_fields, _snapshot_delete_pod  # noqa: E402


def test_apply_manifest_blocked_when_human_rejects():
    spec = SysadminAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="apply-1")
    by_name = {t.name: t for t in gated}

    with patch("sysadmin.tools.subprocess.run") as run_mock:
        result = by_name["apply_manifest"].invoke({
            "yaml": "apiVersion: v1\nkind: Pod\nmetadata:\n  name: x\n",
            "namespace": "default",
        })

    assert "REJECTED" in result
    run_mock.assert_not_called()


def test_apply_manifest_pipes_yaml_to_kubectl_stdin_on_approval():
    """When approved, apply_manifest must:
      1. invoke `kubectl apply -n NS -f -`
      2. pass the YAML body via stdin (input=...)
    Both are observable on the mocked subprocess.run call."""
    spec = SysadminAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="apply-2")
    by_name = {t.name: t for t in gated}

    yaml_body = "apiVersion: v1\nkind: Pod\nmetadata:\n  name: web-x\n"
    with patch("sysadmin.tools.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="pod/web-x created\n", stderr="",
        )
        out = by_name["apply_manifest"].invoke({
            "yaml": yaml_body, "namespace": "production",
        })

    assert "created" in out
    args, kwargs = run_mock.call_args
    assert args[0] == ["kubectl", "apply", "-n", "production", "-f", "-"]
    assert kwargs.get("input") == yaml_body


def test_apply_manifest_rejects_empty_yaml_without_calling_kubectl():
    spec = SysadminAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="apply-3")
    by_name = {t.name: t for t in gated}

    with patch("sysadmin.tools.subprocess.run") as run_mock:
        out = by_name["apply_manifest"].invoke({"yaml": "   ", "namespace": "default"})
    assert "empty manifest" in out.lower()
    run_mock.assert_not_called()


def test_apply_manifest_surfaces_nonzero_exit_with_stderr():
    spec = SysadminAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="apply-4")
    by_name = {t.name: t for t in gated}

    with patch("sysadmin.tools.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error: bad manifest\n",
        )
        out = by_name["apply_manifest"].invoke({"yaml": "garbage", "namespace": "default"})

    assert "EXIT=1" in out
    assert "bad manifest" in out


def test_delete_pod_snapshot_captures_manifest_via_kubectl_get():
    """The snapshot fn must run BEFORE the destructive delete, calling
    `kubectl get pod -o yaml` and stashing the cleaned manifest as
    the inverse-call's yaml argument."""
    pod_yaml = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: web-x\n"
        "  namespace: default\n"
        "  uid: aaa-bbb-ccc\n"
        "  resourceVersion: '123'\n"
        "  labels:\n"
        "    app: web\n"
        "spec:\n"
        "  containers:\n"
        "  - name: c\n"
        "    image: nginx:alpine\n"
        "status:\n"
        "  phase: Running\n"
        "  podIP: 10.0.0.1\n"
    )
    with patch("sysadmin.tools.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=pod_yaml, stderr="",
        )
        plan = _snapshot_delete_pod({"name": "web-x", "namespace": "default"})

    assert plan.inverse_tool == "apply_manifest"
    assert plan.inverse_args["namespace"] == "default"
    yaml = plan.inverse_args["yaml"]
    # Pod identity survives.
    assert "name: web-x" in yaml
    assert "image: nginx:alpine" in yaml
    # Server-managed fields are scrubbed.
    assert "uid:" not in yaml
    assert "resourceVersion:" not in yaml
    assert "status:" not in yaml
    assert "podIP:" not in yaml
    # Description is descriptive.
    assert "web-x" in plan.description
    assert plan.snapshot["captured"] is True


def test_delete_pod_snapshot_kubectl_failure_returns_noop_plan():
    """If kubectl get fails (e.g. pod already gone), the plan must
    still come back — but flagged so the user can see the rollback
    isn't actually capturable."""
    with patch("sysadmin.tools.subprocess.run") as run_mock:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error from server (NotFound): pods 'gone' not found\n",
        )
        plan = _snapshot_delete_pod({"name": "gone", "namespace": "default"})

    assert plan.snapshot["captured"] is False
    assert "NO-OP" in plan.description
    assert plan.inverse_args["yaml"] == ""


def test_scrub_server_fields_removes_status_block_entirely():
    raw = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "spec:\n"
        "  containers: []\n"
        "status:\n"
        "  phase: Running\n"
        "  conditions: []\n"
    )
    cleaned = _scrub_server_fields(raw)
    assert "status:" not in cleaned
    assert "phase: Running" not in cleaned
    assert "spec:" in cleaned
    assert "kind: Pod" in cleaned


def test_scrub_server_fields_strips_managed_fields_from_metadata():
    raw = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: keep\n"
        "  uid: drop-me\n"
        "  resourceVersion: '99'\n"
        "  managedFields:\n"
        "    - manager: kube\n"
        "      operation: Update\n"
        "  labels:\n"
        "    app: keep\n"
        "spec:\n"
        "  containers: []\n"
    )
    cleaned = _scrub_server_fields(raw)
    assert "name: keep" in cleaned
    assert "app: keep" in cleaned
    assert "uid:" not in cleaned
    assert "resourceVersion:" not in cleaned
    assert "managedFields:" not in cleaned


def test_delete_pod_round_trip_with_rollback_capture():
    """End-to-end: gate_tools wraps delete_pod, approval is granted,
    the snapshot fn captures the manifest, and the rollback entry
    lands in the store with the right inverse args."""
    pod_yaml = (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata:\n"
        "  name: target\n"
        "  namespace: default\n"
        "spec:\n"
        "  containers:\n"
        "  - name: c\n"
        "    image: nginx\n"
    )
    spec = SysadminAgent()
    store = _InMemRb()
    ctx = AgentContext(
        approval=AlwaysApprove(),
        audit=InMemoryAuditLogger(),
        rollback=store,
    )
    gated = gate_tools(spec, ctx, task_id="rb-end-to-end")
    by_name = {t.name: t for t in gated}

    # subprocess.run gets called twice: once by the snapshot fn (get),
    # once by the forward call (delete). Both need to succeed.
    call_count = {"n": 0}

    def fake_run(cmd, **kw):
        call_count["n"] += 1
        if cmd[:2] == ["kubectl", "get"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=pod_yaml, stderr="")
        if cmd[:2] == ["kubectl", "delete"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='pod "target" deleted\n', stderr="")
        raise AssertionError(f"unexpected kubectl call: {cmd}")

    with patch("sysadmin.tools.subprocess.run", side_effect=fake_run):
        out = by_name["delete_pod"].invoke({"name": "target", "namespace": "default"})

    assert "deleted" in out
    assert call_count["n"] == 2  # snapshot get + forward delete

    # Rollback was captured against the right task_id.
    entries = store.list_for_task("rb-end-to-end")
    assert len(entries) == 1
    e = entries[0]
    assert e.forward_tool == "delete_pod"
    assert e.inverse_tool == "apply_manifest"
    assert "name: target" in e.inverse_args["yaml"]
    assert e.inverse_args["namespace"] == "default"
