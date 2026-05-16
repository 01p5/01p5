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


# ---------------------------------------------------------------------------
# Edge cases — read_file, edit_file, write_file
# ---------------------------------------------------------------------------


def test_read_file_offset_past_end_returns_explanatory_message(tmp_path):
    """Past-end-of-file offset returns a clear `past end of file` string,
    not a crash and not an empty string."""
    target = tmp_path / "short.txt"
    target.write_text("only one line\n")
    spec = ProgrammerAgent()
    ctx, _ = _ctx()
    gated = gate_tools(spec, ctx, task_id="t-pe")
    by_name = {t.name: t for t in gated}
    out = by_name["read_file"].invoke({"path": str(target), "offset": 50})
    assert "past end of file" in out
    assert "total lines = 1" in out


def test_read_file_binary_returns_utf8_error(tmp_path):
    """Binary content trips UnicodeDecodeError → tool surfaces an
    error message instead of raising."""
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\xff\x00garbage\xc3\x28")
    spec = ProgrammerAgent()
    ctx, _ = _ctx()
    gated = gate_tools(spec, ctx, task_id="t-bin")
    by_name = {t.name: t for t in gated}
    out = by_name["read_file"].invoke({"path": str(target)})
    assert "not valid UTF-8" in out


def test_read_file_limit_truncates_with_continuation_hint(tmp_path):
    """When limit < total lines, the output ends with a hint at the
    next offset to read from."""
    target = tmp_path / "long.txt"
    target.write_text("\n".join(f"line{i}" for i in range(20)) + "\n")
    spec = ProgrammerAgent()
    ctx, _ = _ctx()
    gated = gate_tools(spec, ctx, task_id="t-lim")
    by_name = {t.name: t for t in gated}
    out = by_name["read_file"].invoke({"path": str(target), "limit": 5})
    assert "more lines" in out
    assert "offset=5" in out
    # First 5 lines numbered 1..5 are present, line 6 is not.
    assert "1 | line0" in out
    assert "5 | line4" in out
    assert "6 | line5" not in out


def test_read_file_nonexistent_path_returns_error(tmp_path):
    """Missing file → clean ERROR string, no crash."""
    spec = ProgrammerAgent()
    ctx, _ = _ctx()
    gated = gate_tools(spec, ctx, task_id="t-mis")
    by_name = {t.name: t for t in gated}
    out = by_name["read_file"].invoke({"path": str(tmp_path / "nope.txt")})
    assert "ERROR" in out
    assert "does not exist" in out


def test_edit_file_noop_same_old_and_new_string(tmp_path):
    """old_string == new_string is a no-op and rejected with a clear
    error — file remains untouched."""
    target = tmp_path / "noop.txt"
    target.write_text("hello\n")
    spec = ProgrammerAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="t-noop")
    by_name = {t.name: t for t in gated}
    out = by_name["edit_file"].invoke({
        "path": str(target),
        "old_string": "hello",
        "new_string": "hello",
    })
    assert "ERROR" in out
    assert "no-op" in out
    assert target.read_text() == "hello\n"


def test_edit_file_nonexistent_path_returns_use_write_file_hint(tmp_path):
    """edit_file on a missing file: tool returns the 'use write_file'
    error. The runtime still routes through approval (since the
    destructive verb fires before the tool body executes), but the
    underlying tool's error message must reach the caller."""
    spec = ProgrammerAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="t-missedit")
    by_name = {t.name: t for t in gated}
    out = by_name["edit_file"].invoke({
        "path": str(tmp_path / "ghost.txt"),
        "old_string": "x",
        "new_string": "y",
    })
    assert "ERROR" in out
    assert "use write_file" in out


def test_write_file_creates_nested_directories(tmp_path):
    """mkdir(parents=True) — write_file should auto-create intermediate
    directories."""
    spec = ProgrammerAgent()
    ctx, _ = _ctx(approval=AlwaysApprove())
    gated = gate_tools(spec, ctx, task_id="t-nest")
    by_name = {t.name: t for t in gated}
    target = tmp_path / "a" / "b" / "c" / "deep.txt"
    out = by_name["write_file"].invoke({"path": str(target), "content": "deep\n"})
    assert "wrote" in out
    assert target.read_text() == "deep\n"
    assert (tmp_path / "a" / "b" / "c").is_dir()


def test_write_file_diff_snoop_new_file_has_all_plus_lines(tmp_path):
    """The diff kwarg passed to approval.request for a NEW file should
    contain only additions (no `-` lines)."""
    spec = ProgrammerAgent()
    seen: dict = {}

    class _Snoop:
        def request(self, **kw):
            from agentlib import ApprovalDecision
            seen.update(kw)
            return ApprovalDecision(approved=False, reason="snoop")

    ctx = AgentContext(approval=_Snoop(), audit=InMemoryAuditLogger())
    gated = gate_tools(spec, ctx, task_id="t-wf-new")
    by_name = {t.name: t for t in gated}
    target = tmp_path / "brand_new.py"
    by_name["write_file"].invoke({
        "path": str(target),
        "content": "print('hello')\nprint('world')\n",
    })
    assert "diff" in seen
    diff = seen["diff"] or ""
    assert "+print('hello')" in diff
    assert "+print('world')" in diff
    # No `-` body lines (it's a new file).
    minus_body = [
        ln for ln in diff.splitlines()
        if ln.startswith("-") and not ln.startswith("---")
    ]
    assert minus_body == []


def test_write_file_diff_snoop_existing_file_shows_real_diff(tmp_path):
    """For an existing file, the diff kwarg should contain BOTH - and
    + lines reflecting the actual change."""
    spec = ProgrammerAgent()
    target = tmp_path / "existing.py"
    target.write_text("print('old')\n")
    seen: dict = {}

    class _Snoop:
        def request(self, **kw):
            from agentlib import ApprovalDecision
            seen.update(kw)
            return ApprovalDecision(approved=False, reason="snoop")

    ctx = AgentContext(approval=_Snoop(), audit=InMemoryAuditLogger())
    gated = gate_tools(spec, ctx, task_id="t-wf-exist")
    by_name = {t.name: t for t in gated}
    by_name["write_file"].invoke({
        "path": str(target),
        "content": "print('new')\n",
    })
    diff = seen.get("diff") or ""
    assert "-print('old')" in diff
    assert "+print('new')" in diff
    # File untouched (rejected).
    assert target.read_text() == "print('old')\n"
