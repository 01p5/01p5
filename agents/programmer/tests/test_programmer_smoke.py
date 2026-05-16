"""
Smoke tests for the Programmer agent.

Programmer's destructive surface is ``write_file`` (file-system mutation);
its read-only surface is the in-memory artifact templating.
"""
from __future__ import annotations

from agentlib import (
    AgentContext,
    AlwaysApprove,
    AlwaysReject,
    InMemoryAuditLogger,
    gate_tools,
)
from programmer.agent import ProgrammerAgent


def _ctx(approval=None):
    audit = InMemoryAuditLogger()
    return AgentContext(approval=approval or AlwaysApprove(), audit=audit), audit


def test_programmer_declares_destructive_verbs_correctly():
    spec = ProgrammerAgent()
    assert spec.destructive_verbs == {"write_file", "edit_file"}
    declared = {t.name for t in spec.tools}
    assert "generate_dockerfile" in declared
    assert "write_file" in declared
    assert "read_file" in declared
    assert "edit_file" in declared


def test_read_file_returns_numbered_lines(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    spec = ProgrammerAgent()
    ctx, _ = _ctx()
    gated = gate_tools(spec, ctx, task_id="t-rd")
    by_name = {t.name: t for t in gated}
    out = by_name["read_file"].invoke({"path": str(target)})
    assert "1 | alpha" in out
    assert "2 | beta" in out
    assert "3 | gamma" in out


def test_edit_file_replaces_unique_match(tmp_path):
    target = tmp_path / "config.tf"
    target.write_text('region = "us-west-1"\n')
    spec = ProgrammerAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="t-ed")
    by_name = {t.name: t for t in gated}
    out = by_name["edit_file"].invoke({
        "path": str(target),
        "old_string": 'region = "us-west-1"',
        "new_string": 'region = "us-east-2"',
    })
    assert "edited" in out
    assert target.read_text() == 'region = "us-east-2"\n'


def test_edit_file_errors_on_ambiguous_match_without_replace_all(tmp_path):
    target = tmp_path / "foo.tf"
    target.write_text("foo\nfoo\nfoo\n")
    spec = ProgrammerAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="t-amb")
    by_name = {t.name: t for t in gated}
    out = by_name["edit_file"].invoke({
        "path": str(target),
        "old_string": "foo",
        "new_string": "bar",
    })
    assert "matches 3 places" in out
    # File untouched.
    assert target.read_text() == "foo\nfoo\nfoo\n"
    # Replace_all=True succeeds.
    out2 = by_name["edit_file"].invoke({
        "path": str(target),
        "old_string": "foo",
        "new_string": "bar",
        "replace_all": True,
    })
    assert "edited" in out2 and "3 replacements" in out2
    assert target.read_text() == "bar\nbar\nbar\n"


def test_edit_file_blocked_when_human_rejects(tmp_path):
    target = tmp_path / "stay.tf"
    target.write_text("before\n")
    spec = ProgrammerAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="t-rej")
    by_name = {t.name: t for t in gated}
    out = by_name["edit_file"].invoke({
        "path": str(target),
        "old_string": "before",
        "new_string": "after",
    })
    assert "REJECTED" in out
    # File untouched.
    assert target.read_text() == "before\n"
    # Audit recorded the rejection.
    assert any(r["tool"] == "edit_file" and r["approved"] is False for r in audit.records)


def test_diff_preview_passed_to_approval_hook(tmp_path):
    """Verify the diff preview makes it into the ApprovalHook call —
    the whole point of edit_file is that the approval card shows
    exactly which lines change, not just the raw new_string."""
    target = tmp_path / "diff-test.tf"
    target.write_text('old_value = 1\nstable = "yes"\n')
    spec = ProgrammerAgent()
    seen = {}

    class _Snoop:
        def request(self, **kw):
            from agentlib import ApprovalDecision
            seen.update(kw)
            return ApprovalDecision(approved=False, reason="snoop")

    ctx = AgentContext(approval=_Snoop(), audit=InMemoryAuditLogger())
    gated = gate_tools(spec, ctx, task_id="t-diff")
    by_name = {t.name: t for t in gated}
    by_name["edit_file"].invoke({
        "path": str(target),
        "old_string": "old_value = 1",
        "new_string": "old_value = 42",
    })
    assert "diff" in seen
    diff = seen["diff"] or ""
    assert "-old_value = 1" in diff
    assert "+old_value = 42" in diff
    assert "stable = " not in diff or " stable" in diff  # context line shown


def test_dockerfile_template_runs_without_approval():
    spec = ProgrammerAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="prog-smoke-1")
    by_name = {t.name: t for t in gated}

    out = by_name["generate_dockerfile"].invoke(
        {"language": "python", "version": "3.12", "cmd": ["python", "app.py"]}
    )
    assert "FROM python:3.12-slim" in out
    assert audit.records[0]["approved"] is None


def test_write_file_blocked_when_human_rejects(tmp_path):
    spec = ProgrammerAgent()
    ctx, audit = _ctx(approval=AlwaysReject())
    gated = gate_tools(spec, ctx, task_id="prog-smoke-2")
    by_name = {t.name: t for t in gated}

    target = tmp_path / "Dockerfile"
    result = by_name["write_file"].invoke({"path": str(target), "content": "FROM scratch"})

    assert "REJECTED" in result
    assert not target.exists()
    assert audit.records[0]["approved"] is False


def test_write_file_runs_after_approval(tmp_path):
    spec = ProgrammerAgent()
    ctx, audit = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="prog-smoke-3")
    by_name = {t.name: t for t in gated}

    target = tmp_path / "subdir" / "Dockerfile"
    result = by_name["write_file"].invoke({"path": str(target), "content": "FROM scratch"})

    assert "wrote" in result
    assert target.read_text() == "FROM scratch"
    tools = [r["tool"] for r in audit.records]
    assert tools == ["write_file", "write_file"]
