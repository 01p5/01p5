"""Cover SysadminAgent.handle() — both success and exception branches.

The LLM call (StructuralAgent.invoke) is fully mocked. We only verify
that handle() assembles the right AgentResult, captures cost, and
catches exceptions into a failed result rather than propagating.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentlib import AgentContext, AlwaysApprove, InMemoryAuditLogger, TaskMessage
from sysadmin.agent import SysadminAgent, SysadminResponse


def _ctx():
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_handle_success_assembles_agent_result():
    spec = SysadminAgent()
    response = SysadminResponse(
        summary="found 3 pods running",
        findings={"running": 3, "failing": 0},
        actions_taken=["get_pods"],
    )
    fake_agent = MagicMock()
    fake_agent.invoke.return_value = response
    with patch("sysadmin.agent.StructuralAgent", return_value=fake_agent), \
         patch("sysadmin.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="status"), _ctx())
    assert result.status == "success"
    assert result.summary == "found 3 pods running"
    assert result.artifacts["findings"]["running"] == 3
    assert result.artifacts["actions_taken"] == ["get_pods"]
    fake_agent.cleanup.assert_called_once()


def test_handle_exception_returns_failed_result_with_type_in_summary():
    spec = SysadminAgent()
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = RuntimeError("kube unreachable")
    with patch("sysadmin.agent.StructuralAgent", return_value=fake_agent), \
         patch("sysadmin.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="status"), _ctx())
    assert result.status == "failed"
    assert "RuntimeError" in result.summary
    assert "kube unreachable" in result.summary
    # cleanup() must still run even on the error path.
    fake_agent.cleanup.assert_called_once()
