"""Cover AnsibleAgent.handle() success + exception branches."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentlib import AgentContext, AlwaysApprove, InMemoryAuditLogger, TaskMessage
from ansible_agent.agent import AnsibleAgent, AnsibleResponse


def _ctx():
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_handle_success_with_check_summary_and_actions():
    spec = AnsibleAgent()
    response = AnsibleResponse(
        summary="checked then applied",
        check_summary="no diff",
        actions_taken=["run_playbook"],
        findings={"changed": 0, "ok": 3},
    )
    fake_agent = MagicMock()
    fake_agent.invoke.return_value = response
    with patch("ansible_agent.agent.StructuralAgent", return_value=fake_agent), \
         patch("ansible_agent.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="apply x"), _ctx())
    assert result.status == "success"
    assert result.artifacts["check_summary"] == "no diff"
    assert result.artifacts["actions_taken"] == ["run_playbook"]
    assert result.artifacts["findings"]["ok"] == 3


def test_handle_exception_returns_failed():
    spec = AnsibleAgent()
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = RuntimeError("ansible not installed")
    with patch("ansible_agent.agent.StructuralAgent", return_value=fake_agent), \
         patch("ansible_agent.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="x"), _ctx())
    assert result.status == "failed"
    assert "RuntimeError" in result.summary
