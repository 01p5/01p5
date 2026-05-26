"""HPC agent smoke test — class metadata + handle() success / error.

LLM call is fully mocked. We only check the agent's declared shape
(name, prerequisites, no native tools, destructive verbs set) and
that handle() wires through StructuralAgent the same way the other
agents do.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentlib import AgentContext, AlwaysApprove, InMemoryAuditLogger, TaskMessage
from hpc.agent import HPCAgent, HPCResponse


def _ctx():
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_hpc_class_metadata_declares_mcp_prerequisites():
    spec = HPCAgent()
    assert spec.name == "hpc"
    # Both MCP servers must be wired before the router considers HPC.
    assert spec.prerequisites == {"gpu-mcp", "slurm-mcp"}
    # No native tools — everything is MCP-grafted at runtime.
    assert list(spec.tools) == []
    # Destructive verbs pre-declared so the runtime gates them even
    # before the MCP wiring runs its own destructive merge.
    assert "slurm_jobs_cancel" in spec.destructive_verbs
    assert "slurm_jobs_hold" in spec.destructive_verbs


def test_hpc_handle_success_returns_findings_and_actions():
    spec = HPCAgent()
    response = HPCResponse(
        summary="GPU fleet healthy; 3 jobs running",
        findings={"healthy_nodes": 4, "draining": 0},
        actions_taken=[],
    )
    fake_agent = MagicMock()
    fake_agent.invoke.return_value = response
    with patch("hpc.agent.StructuralAgent", return_value=fake_agent), \
         patch("hpc.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="status"), _ctx())
    assert result.status == "success"
    assert result.summary.startswith("GPU fleet healthy")
    assert result.artifacts["findings"]["healthy_nodes"] == 4
    assert result.artifacts["actions_taken"] == []
    fake_agent.cleanup.assert_called_once()


def test_hpc_handle_exception_returns_failed_with_type_name():
    spec = HPCAgent()
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = RuntimeError("slurm controller unreachable")
    with patch("hpc.agent.StructuralAgent", return_value=fake_agent), \
         patch("hpc.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="x"), _ctx())
    assert result.status == "failed"
    assert "RuntimeError" in result.summary
    assert "slurm controller unreachable" in result.summary
    fake_agent.cleanup.assert_called_once()
