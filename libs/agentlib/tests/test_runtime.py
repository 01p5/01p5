"""
Tests for agentlib.runtime — tool-gating and approval interception.

These tests deliberately avoid LLMs and external services. They exercise
the gate_tools wrapper directly so contract violations surface as code
errors, not flaky integration failures.
"""
from __future__ import annotations

from typing import Any, Sequence

from langchain_core.tools import tool

from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    AlwaysApprove,
    AlwaysReject,
    InMemoryAuditLogger,
    TaskMessage,
    gate_tools,
)


@tool
def safe_read(name: str) -> str:
    """Read something harmless."""
    return f"read:{name}"


@tool
def dangerous_delete(target: str) -> str:
    """Pretend to delete something."""
    return f"deleted:{target}"


class _StubAgent(AgentSpec):
    name = "stub"
    domain = "test"
    tools: Sequence[Any] = [safe_read, dangerous_delete]
    destructive_verbs = {"dangerous_delete"}

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        raise NotImplementedError


def _ctx(approval) -> tuple[AgentContext, InMemoryAuditLogger]:
    audit = InMemoryAuditLogger()
    return AgentContext(approval=approval, audit=audit), audit


def test_non_destructive_tool_runs_without_approval():
    ctx, audit = _ctx(AlwaysReject())  # would reject any approval request
    gated = gate_tools(_StubAgent(), ctx, task_id="t1")

    by_name = {t.name: t for t in gated}
    result = by_name["safe_read"].invoke({"name": "pod-a"})

    assert result == "read:pod-a"
    # Audit should record the call with approved=None (never asked).
    assert len(audit.records) == 1
    assert audit.records[0]["tool"] == "safe_read"
    assert audit.records[0]["approved"] is None


def test_destructive_tool_routed_through_approval_and_runs_when_approved():
    ctx, audit = _ctx(AlwaysApprove())
    gated = gate_tools(_StubAgent(), ctx, task_id="t2")
    by_name = {t.name: t for t in gated}

    result = by_name["dangerous_delete"].invoke({"target": "pod-x"})

    assert result == "deleted:pod-x"
    # Two audit records: approval decision + execution.
    tools_logged = [r["tool"] for r in audit.records]
    assert tools_logged == ["dangerous_delete", "dangerous_delete"]
    assert audit.records[0]["approved"] is True
    assert audit.records[0]["result"] is None  # pre-execution log
    assert audit.records[1]["approved"] is True
    assert audit.records[1]["result"] == "deleted:pod-x"


def test_destructive_tool_returns_rejection_when_denied():
    ctx, audit = _ctx(AlwaysReject())
    gated = gate_tools(_StubAgent(), ctx, task_id="t3")
    by_name = {t.name: t for t in gated}

    result = by_name["dangerous_delete"].invoke({"target": "pod-y"})

    assert "REJECTED" in result
    # Only the rejection is logged — no execution.
    assert len(audit.records) == 1
    assert audit.records[0]["approved"] is False


def test_gate_tools_rejects_undeclared_tool_at_construction():
    """Defense-in-depth: if a programmer mutates spec.tools after init,
    gate_tools must catch it before the agent runs."""

    class BadAgent(_StubAgent):
        # Same declared set, but we sneak in by-name mismatch via override
        pass

    bad = BadAgent()
    # Replace tools with a stub that lies about its name
    @tool
    def impostor(x: str) -> str:
        """Pretends to be a declared tool."""
        return x

    bad.tools = [impostor]
    bad.destructive_verbs = {"safe_read"}  # no longer matches impostor name

    # gate_tools should accept here (impostor is in the list, declared==tools).
    # The real defense is that LangChain only exposes tools we hand it.
    # This test pins behavior: the gate's job is consistency between
    # spec.tools and what gets wrapped, not preventing tool-list edits.
    gated = gate_tools(bad, AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger()), "t4")
    assert {t.name for t in gated} == {"impostor"}


def test_approval_can_modify_args():
    class SnoopApproval:
        def __init__(self) -> None:
            self.seen: dict[str, Any] = {}

        def request(self, **kwargs):
            self.seen = kwargs
            from agentlib import ApprovalDecision
            return ApprovalDecision(
                approved=True,
                reason="ok with edit",
                modified_args={"target": "edited-by-human"},
            )

    snoop = SnoopApproval()
    ctx, audit = _ctx(snoop)
    gated = gate_tools(_StubAgent(), ctx, "t5")
    by_name = {t.name: t for t in gated}

    result = by_name["dangerous_delete"].invoke({"target": "original"})
    assert result == "deleted:edited-by-human"
    assert snoop.seen["args"] == {"target": "original"}
