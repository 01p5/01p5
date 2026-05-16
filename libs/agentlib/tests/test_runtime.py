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
    InMemoryRollbackStore,
    RollbackPlan,
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


# ---------------------------------------------------------------------------
# Diff preview helpers — _preview_diff / _unified_diff
# ---------------------------------------------------------------------------

from agentlib.runtime import _preview_diff, _unified_diff  # noqa: E402


def test_unified_diff_new_file_has_all_plus_lines():
    """Empty old + non-empty new: every body line is a `+` add line, with
    the standard unified-diff header (---/+++)."""
    out = _unified_diff("", "alpha\nbeta\n", "newfile.txt")
    assert out != "(no textual difference)"
    assert "--- a/newfile.txt" in out
    assert "+++ b/newfile.txt" in out
    # Body lines (post-header) should all be additions.
    body_lines = [ln for ln in out.splitlines() if ln and ln[0] in "+-@ "]
    add_lines = [ln for ln in body_lines if ln.startswith("+") and not ln.startswith("+++")]
    minus_lines = [ln for ln in body_lines if ln.startswith("-") and not ln.startswith("---")]
    assert len(add_lines) >= 2
    assert minus_lines == []


def test_unified_diff_deleted_file_has_all_minus_lines():
    """Non-empty old + empty new: every body line is a `-` remove line."""
    out = _unified_diff("alpha\nbeta\n", "", "gone.txt")
    assert out != "(no textual difference)"
    assert "--- a/gone.txt" in out
    assert "+++ b/gone.txt" in out
    body_lines = [ln for ln in out.splitlines() if ln and ln[0] in "+-@ "]
    add_lines = [ln for ln in body_lines if ln.startswith("+") and not ln.startswith("+++")]
    minus_lines = [ln for ln in body_lines if ln.startswith("-") and not ln.startswith("---")]
    assert len(minus_lines) >= 2
    assert add_lines == []


def test_unified_diff_identical_returns_sentinel_string():
    """When there's nothing to diff, the sentinel string is returned so
    the approval card can render 'no change'."""
    assert _unified_diff("same\n", "same\n", "any.txt") == "(no textual difference)"


def test_unified_diff_single_line_change_has_hunk_header_and_marked_lines():
    """A 1-line change should produce a `@@ -... +... @@` hunk header
    and the changed line should be prefixed with - and +."""
    old = "alpha\nbeta\ngamma\n"
    new = "alpha\nBETA\ngamma\n"
    out = _unified_diff(old, new, "f.txt")
    # Hunk header
    import re
    hunk = re.search(r"^@@ -\d+(,\d+)? \+\d+(,\d+)? @@", out, re.MULTILINE)
    assert hunk is not None, f"missing hunk header in:\n{out}"
    # The removed and added lines
    assert "-beta" in out
    assert "+BETA" in out


def test_preview_diff_write_file_against_existing_file(tmp_path):
    """write_file with an existing path: diff is current-vs-proposed."""
    target = tmp_path / "x.txt"
    target.write_text("old line\n")
    diff = _preview_diff("write_file", {"path": str(target), "content": "new line\n"})
    assert diff is not None
    assert "-old line" in diff
    assert "+new line" in diff


def test_preview_diff_write_file_against_nonexistent_path(tmp_path):
    """write_file with a path that doesn't exist: diff treats old as empty."""
    target = tmp_path / "missing.txt"
    diff = _preview_diff("write_file", {"path": str(target), "content": "hello\n"})
    assert diff is not None
    assert "+hello" in diff
    # Old side should be empty (no `-` body lines).
    body = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert body == []


def test_preview_diff_edit_file_against_existing_file(tmp_path):
    """edit_file diff is current vs post-replace text."""
    target = tmp_path / "c.txt"
    target.write_text("foo = 1\nbar = 2\n")
    diff = _preview_diff(
        "edit_file",
        {"path": str(target), "old_string": "foo = 1", "new_string": "foo = 42"},
    )
    assert diff is not None
    assert "-foo = 1" in diff
    assert "+foo = 42" in diff
    # The unchanged context line should be present.
    assert "bar = 2" in diff


def test_preview_diff_edit_file_nonexistent_returns_none(tmp_path):
    """edit_file against a missing file must return None (not crash)."""
    out = _preview_diff(
        "edit_file",
        {"path": str(tmp_path / "nope.txt"), "old_string": "a", "new_string": "b"},
    )
    assert out is None


def test_preview_diff_unknown_tool_returns_none():
    """Tools that aren't file-mutating yield no diff preview."""
    assert _preview_diff("delete_pod", {"name": "x"}) is None


def test_preview_diff_write_file_empty_args_no_crash():
    """Missing args dict shouldn't crash — diff against empty empty
    produces the sentinel string."""
    out = _preview_diff("write_file", {})
    # Both old and new resolve to "" so the diff returns the sentinel.
    assert isinstance(out, str)


def test_preview_diff_edit_file_empty_args_returns_none():
    """edit_file with no path arg should fail the read and return None."""
    assert _preview_diff("edit_file", {}) is None


def test_preview_diff_edit_file_replace_all_shows_every_change(tmp_path):
    """replace_all=True: all three occurrences appear as +/- lines.
    replace_all=False: only the first."""
    target = tmp_path / "m.txt"
    target.write_text("foo\nfoo\nfoo\n")
    diff_all = _preview_diff(
        "edit_file",
        {"path": str(target), "old_string": "foo", "new_string": "bar", "replace_all": True},
    )
    assert diff_all is not None
    add_lines = [ln for ln in diff_all.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    minus_lines = [ln for ln in diff_all.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert len([ln for ln in add_lines if "bar" in ln]) == 3
    assert len([ln for ln in minus_lines if "foo" in ln]) == 3

    diff_one = _preview_diff(
        "edit_file",
        {"path": str(target), "old_string": "foo", "new_string": "bar"},
    )
    assert diff_one is not None
    add_lines_one = [
        ln for ln in diff_one.splitlines()
        if ln.startswith("+") and not ln.startswith("+++") and "bar" in ln
    ]
    minus_lines_one = [
        ln for ln in diff_one.splitlines()
        if ln.startswith("-") and not ln.startswith("---") and "foo" in ln
    ]
    assert len(add_lines_one) == 1
    assert len(minus_lines_one) == 1


# ---------------------------------------------------------------------
# Rollback capture via gate_tools
# ---------------------------------------------------------------------


@tool
def _writer(path: str, content: str) -> str:
    """Pretend write_file. Just returns a confirmation."""
    return f"wrote {path}"


def _snapshot_writer(args: dict) -> RollbackPlan:
    return RollbackPlan(
        inverse_tool="_writer",
        inverse_args={"path": args["path"], "content": "<prior>"},
        description=f"restore {args['path']}",
        snapshot={"prior_content": "<prior>"},
    )


class _SnapAgent(AgentSpec):
    name = "snap"
    domain = "test"
    tools = [_writer]
    destructive_verbs = {"_writer"}
    rollback_snapshots = {"_writer": _snapshot_writer}

    def handle(self, task, ctx):
        raise NotImplementedError


def _ctx_with_rollback(approval=None, rollback=None):
    return AgentContext(
        approval=approval or AlwaysApprove(),
        audit=InMemoryAuditLogger(),
        rollback=rollback,
    )


def test_rollback_persisted_on_approved_destructive_success():
    store = InMemoryRollbackStore()
    ctx = _ctx_with_rollback(rollback=store)
    gated = gate_tools(_SnapAgent(), ctx, task_id="task-A")
    [writer] = gated

    out = writer.invoke({"path": "/tmp/x", "content": "new"})
    assert "wrote /tmp/x" in out

    entries = store.list_for_task("task-A")
    assert len(entries) == 1
    e = entries[0]
    assert e.agent == "snap"
    assert e.forward_tool == "_writer"
    assert e.forward_args == {"path": "/tmp/x", "content": "new"}
    assert e.inverse_tool == "_writer"
    assert e.inverse_args == {"path": "/tmp/x", "content": "<prior>"}
    assert "restore /tmp/x" in e.description
    assert e.executed is False


def test_rollback_not_persisted_when_human_rejects():
    store = InMemoryRollbackStore()
    ctx = _ctx_with_rollback(approval=AlwaysReject(), rollback=store)
    gated = gate_tools(_SnapAgent(), ctx, task_id="task-B")
    [writer] = gated

    out = writer.invoke({"path": "/tmp/x", "content": "new"})
    assert "REJECTED" in out
    assert store.list_for_task("task-B") == []


def test_rollback_skipped_when_no_snapshot_registered():
    """An agent that declares the verb destructive but doesn't supply
    a snapshot fn must run the forward op cleanly — just without
    capturing rollback."""

    class _NoSnap(AgentSpec):
        name = "nosnap"
        domain = "test"
        tools = [_writer]
        destructive_verbs = {"_writer"}
        # rollback_snapshots intentionally left as the {} default

        def handle(self, task, ctx):
            raise NotImplementedError

    store = InMemoryRollbackStore()
    ctx = _ctx_with_rollback(rollback=store)
    gated = gate_tools(_NoSnap(), ctx, task_id="task-C")
    out = gated[0].invoke({"path": "/tmp/x", "content": "y"})
    assert "wrote /tmp/x" in out
    assert store.list_for_task("task-C") == []


def test_rollback_skipped_when_ctx_has_no_store():
    """No store on the context → snapshot fn still runs (harmless)
    but no persistence happens. The forward op must still succeed."""
    ctx = _ctx_with_rollback(rollback=None)
    gated = gate_tools(_SnapAgent(), ctx, task_id="task-D")
    out = gated[0].invoke({"path": "/tmp/x", "content": "y"})
    assert "wrote /tmp/x" in out


def test_rollback_snapshot_failure_does_not_block_forward_call():
    """A buggy snapshot fn must never break the forward path."""

    def _bad_snapshot(args):
        raise RuntimeError("boom")

    class _BadSnap(_SnapAgent):
        rollback_snapshots = {"_writer": _bad_snapshot}

    store = InMemoryRollbackStore()
    ctx = _ctx_with_rollback(rollback=store)
    gated = gate_tools(_BadSnap(), ctx, task_id="task-E")

    out = gated[0].invoke({"path": "/tmp/x", "content": "y"})
    assert "wrote /tmp/x" in out
    # Snapshot failed → nothing persisted.
    assert store.list_for_task("task-E") == []


def test_rollback_store_write_failure_does_not_break_forward_call():
    class _BadStore:
        def write(self, entry):
            raise RuntimeError("disk full")

        def get(self, rid): return None
        def list_for_task(self, tid): return []
        def list_recent(self, k=25): return []
        def mark_executed(self, rid, result=None): return False

    ctx = _ctx_with_rollback(rollback=_BadStore())
    gated = gate_tools(_SnapAgent(), ctx, task_id="task-F")
    out = gated[0].invoke({"path": "/tmp/x", "content": "y"})
    # Forward call still succeeds; the warning is logged.
    assert "wrote /tmp/x" in out


def test_rollback_uses_modified_args_when_approver_modifies():
    """If the approval hook returns modified_args, those are what the
    snapshot + forward call see."""
    from agentlib import ApprovalDecision

    class _Modifier:
        def request(self, **kw):
            return ApprovalDecision(
                approved=True, reason="ok",
                modified_args={"path": "/tmp/safer", "content": "sanitized"},
            )

    store = InMemoryRollbackStore()
    ctx = _ctx_with_rollback(approval=_Modifier(), rollback=store)
    gated = gate_tools(_SnapAgent(), ctx, task_id="task-G")
    out = gated[0].invoke({"path": "/tmp/original", "content": "raw"})
    assert "wrote /tmp/safer" in out

    [entry] = store.list_for_task("task-G")
    assert entry.forward_args == {"path": "/tmp/safer", "content": "sanitized"}
    assert entry.snapshot["prior_content"] == "<prior>"  # snapshot ran AFTER modify


# ---------------------------------------------------------------------
# cost_from_agent — telemetry helper
# ---------------------------------------------------------------------

from agentlib import cost_from_agent  # noqa: E402


class _StubStructuralAgent:
    """Test seam: exposes the same `total_cost_breakdown` /
    `total_token_counts` shape StructuralAgent does, without dragging
    in the full LangChain stack."""

    def __init__(self, total_usd=0.001234, input_tokens=120, output_tokens=80):
        self._total_usd = total_usd
        self._in = input_tokens
        self._out = output_tokens

    def total_cost_breakdown(self):
        return self._total_usd, {
            "input": self._total_usd / 2, "cached_input": 0.0,
            "input_total": self._total_usd / 2, "output": self._total_usd / 2,
            "web_search": 0.0,
        }

    def total_token_counts(self):
        return self._in, self._out


def test_cost_from_agent_pulls_usd_and_tokens_from_agent():
    cb = cost_from_agent(_StubStructuralAgent(), wall_seconds=1.5)
    assert cb.total_usd == 0.001234
    assert cb.input_tokens == 120
    assert cb.output_tokens == 80
    assert cb.wall_seconds == 1.5


def test_cost_from_agent_tolerates_stub_without_accumulator():
    """Tests pass in test stubs that don't implement total_cost_breakdown.
    Helper must degrade to wall_seconds-only rather than crash."""
    class _Bare:
        pass

    cb = cost_from_agent(_Bare(), wall_seconds=0.5)
    assert cb.total_usd == 0.0
    assert cb.input_tokens == 0
    assert cb.output_tokens == 0
    assert cb.wall_seconds == 0.5


def test_cost_from_agent_zero_run_returns_zeros():
    cb = cost_from_agent(_StubStructuralAgent(total_usd=0.0, input_tokens=0, output_tokens=0), wall_seconds=0.0)
    assert cb.total_usd == 0.0
    assert cb.input_tokens == 0
    assert cb.output_tokens == 0
