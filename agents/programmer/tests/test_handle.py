"""Cover ProgrammerAgent.handle() success + exception branches."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentlib import AgentContext, AlwaysApprove, InMemoryAuditLogger, TaskMessage
from programmer.agent import ProgrammerAgent, ProgrammerResponse


def _ctx():
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_handle_success_returns_artifacts_and_files_written():
    spec = ProgrammerAgent()
    response = ProgrammerResponse(
        summary="wrote Dockerfile",
        artifacts={"dockerfile": "FROM python:3.12\n..."},
        files_written=["/tmp/Dockerfile"],
    )
    fake_agent = MagicMock()
    fake_agent.invoke.return_value = response
    with patch("programmer.agent.StructuralAgent", return_value=fake_agent), \
         patch("programmer.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="make Dockerfile"), _ctx())
    assert result.status == "success"
    assert result.artifacts["generated"]["dockerfile"].startswith("FROM python:3.12")
    assert result.artifacts["files_written"] == ["/tmp/Dockerfile"]


def test_handle_exception_returns_failed_with_type_name():
    spec = ProgrammerAgent()
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = ValueError("bad input")
    with patch("programmer.agent.StructuralAgent", return_value=fake_agent), \
         patch("programmer.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="x"), _ctx())
    assert result.status == "failed"
    assert "ValueError" in result.summary
