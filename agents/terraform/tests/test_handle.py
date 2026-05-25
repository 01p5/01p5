"""Cover TerraformAgent.handle() success + exception branches."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentlib import AgentContext, AlwaysApprove, InMemoryAuditLogger, TaskMessage
from terraform.agent import TerraformAgent, TerraformResponse


def _ctx():
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_handle_success_populates_plan_summary_and_findings():
    spec = TerraformAgent()
    response = TerraformResponse(
        summary="planned the aws stack",
        plan_summary="2 to add, 1 to change",
        actions_taken=["tf_plan"],
        findings={"resources_to_add": 2},
    )
    fake_agent = MagicMock()
    fake_agent.invoke.return_value = response
    with patch("terraform.agent.StructuralAgent", return_value=fake_agent), \
         patch("terraform.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="plan aws"), _ctx())
    assert result.status == "success"
    assert result.artifacts["plan_summary"] == "2 to add, 1 to change"
    assert result.artifacts["actions_taken"] == ["tf_plan"]
    assert result.artifacts["findings"]["resources_to_add"] == 2


def test_handle_exception_returns_failed():
    spec = TerraformAgent()
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = RuntimeError("terraform not on PATH")
    with patch("terraform.agent.StructuralAgent", return_value=fake_agent), \
         patch("terraform.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="x"), _ctx())
    assert result.status == "failed"
    assert "RuntimeError" in result.summary
    assert "terraform not on PATH" in result.summary
