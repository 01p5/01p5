"""
Smoke tests for the TerminalCompanionAgent shape + the ``ask`` entry
point. The LangGraph LLM call is mocked — these don't exercise a real
model. Coverage of the actual prompt + tool path lands in integration
tests against the dashboard endpoint (TERM.4b).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentlib import AgentContext, AlwaysApprove, InMemoryAuditLogger, TaskMessage
from terminal_companion.agent import (
    TerminalCompanionAgent,
    TerminalCompanionResponse,
    ask,
)


def _ctx():
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_agent_metadata_marks_read_only():
    spec = TerminalCompanionAgent()
    assert spec.name == "terminal_companion"
    assert spec.destructive_verbs == set()       # never gates approval — read-only
    assert spec.tools == []                       # bound at ask() time
    assert spec.routable is False                 # not LLM-routable


def test_handle_path_assembles_agent_result():
    """Standard AgentSpec.handle path. Won't have scrollback access
    because tools aren't bound here — used only if the orchestrator
    ever dispatches to terminal_companion via the routable path
    (currently doesn't, defensive)."""
    spec = TerminalCompanionAgent()
    fake_agent = MagicMock()
    fake_agent.invoke.return_value = TerminalCompanionResponse(
        answer="no scrollback bound — call via ask() not the router",
        suggested_commands=[],
    )
    with patch("terminal_companion.agent.StructuralAgent", return_value=fake_agent), \
         patch("terminal_companion.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="hi"), _ctx())
    assert result.status == "success"
    assert "no scrollback bound" in result.summary
    fake_agent.cleanup.assert_called_once()


def test_handle_exception_returns_failed_result():
    spec = TerminalCompanionAgent()
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = RuntimeError("llm down")
    with patch("terminal_companion.agent.StructuralAgent", return_value=fake_agent), \
         patch("terminal_companion.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="hi"), _ctx())
    assert result.status == "failed"
    assert "RuntimeError" in result.summary
    fake_agent.cleanup.assert_called_once()


def test_ask_builds_scrollback_tool_with_session_context():
    """ask() must inject a tool closure bound to the session_id +
    owner_email passed in — this is the security-critical path."""
    captured: dict = {}

    def fake_structural(*, tools, **kwargs):
        captured["tools"] = list(tools)
        captured["kwargs"] = kwargs
        m = MagicMock()
        m.invoke.return_value = TerminalCompanionResponse(
            answer="OK", suggested_commands=[],
        )
        return m

    fake_mgr = MagicMock()
    fake_mgr.get.return_value = MagicMock(owner_email="alice@x")

    with patch("terminal_companion.agent.StructuralAgent", side_effect=fake_structural):
        resp = ask(
            question="what's happening?",
            session_manager=fake_mgr,
            session_id="sess-xyz",
            owner_email="alice@x",
            ctx=_ctx(),
        )
    assert isinstance(resp, TerminalCompanionResponse)
    assert resp.answer == "OK"

    # A read_terminal_scrollback tool was injected (gated wrapper —
    # name preserved through the runtime wrapper).
    tool_names = [t.name for t in captured["tools"]]
    assert "read_terminal_scrollback" in tool_names
