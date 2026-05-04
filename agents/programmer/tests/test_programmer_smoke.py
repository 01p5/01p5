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
    assert spec.destructive_verbs == {"write_file"}
    declared = {t.name for t in spec.tools}
    assert "generate_dockerfile" in declared
    assert "write_file" in declared


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
