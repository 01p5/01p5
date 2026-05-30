"""
Phase D — public-web hardening. DEMO_MODE auto-rejects destructive tool
calls (swaps the approval hook for AlwaysReject); OLYMPUS_DAILY_COST_CAP_USD
wires a BudgetGuard into the AgentContext that agents pass to their
StructuralAgent.
"""
from __future__ import annotations

from agentlib import AlwaysReject, BudgetGuard, QueueApprovalHook
from dashboard.server import build_default_server


def _build(tmp_path, monkeypatch, **env) -> object:
    """Construct the dashboard with given env (and a memory/rollback-disabled
    setup so the test stays hermetic)."""
    monkeypatch.setenv("OLYMPUS_MEMORY", "disabled")
    monkeypatch.setenv("OLYMPUS_ROLLBACK", "disabled")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return build_default_server(audit_log_path=str(tmp_path / "a.jsonl"))


def test_demo_mode_swaps_approval_hook_for_always_reject(tmp_path, monkeypatch):
    srv = _build(tmp_path, monkeypatch, OLYMPUS_DEMO_MODE="1")
    assert isinstance(srv.orchestrator.ctx.approval, AlwaysReject)


def test_no_demo_mode_uses_queue_approval(tmp_path, monkeypatch):
    srv = _build(tmp_path, monkeypatch)
    assert isinstance(srv.orchestrator.ctx.approval, QueueApprovalHook)


def test_budget_guard_wired_when_daily_cap_set(tmp_path, monkeypatch):
    srv = _build(tmp_path, monkeypatch, OLYMPUS_DAILY_COST_CAP_USD="1.25")
    guard = srv.orchestrator.ctx.budget_guard
    assert isinstance(guard, BudgetGuard)
    # BudgetGuard exposes its max via _max_budget (internal); also via .remaining_budget.
    assert getattr(guard, "_max_budget", None) == 1.25


def test_no_budget_guard_when_unset(tmp_path, monkeypatch):
    srv = _build(tmp_path, monkeypatch)
    assert srv.orchestrator.ctx.budget_guard is None


def test_invalid_cap_value_ignored(tmp_path, monkeypatch):
    srv = _build(tmp_path, monkeypatch, OLYMPUS_DAILY_COST_CAP_USD="not-a-number")
    assert srv.orchestrator.ctx.budget_guard is None


def test_zero_or_negative_cap_ignored(tmp_path, monkeypatch):
    srv = _build(tmp_path, monkeypatch, OLYMPUS_DAILY_COST_CAP_USD="0")
    assert srv.orchestrator.ctx.budget_guard is None
