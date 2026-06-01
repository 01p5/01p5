"""MainAgent smoke test — metadata, tool wiring from ctx seams, and
handle() success / error. The LLM is fully mocked.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentlib import (
    AgentContext, AlwaysApprove, InMemoryAuditLogger, InMemoryTicketStore,
    TaskMessage, TicketEvent,
)
from main_agent.agent import MainAgent, MainResponse, _conversation_history


def _ctx(dispatcher=None, resolver=None):
    ctx = AgentContext(
        approval=AlwaysApprove(),
        audit=InMemoryAuditLogger(),
        agent_resolver=resolver,
    )
    # dispatcher is a duck-typed seam (not a declared AgentContext field).
    ctx.dispatcher = dispatcher
    return ctx


def test_main_class_metadata():
    spec = MainAgent()
    assert spec.name == "main"
    # The coordinator ships no native tools.
    assert list(spec.tools) == []
    assert spec.model  # a model is declared
    assert "coordinator" in spec.domain.lower()


def _patch_structural(captured: dict):
    """Patch StructuralAgent so we can capture the tools it was built with
    and control invoke()."""
    fake = MagicMock()

    def _factory(*args, **kwargs):
        captured["tools"] = kwargs.get("tools")
        captured["ticket_id"] = kwargs.get("ticket_id")
        return fake

    return fake, _factory


def test_handle_builds_dispatch_and_ask_tools_when_seams_present():
    spec = MainAgent()
    captured: dict = {}
    fake, factory = _patch_structural(captured)
    fake.invoke.return_value = MainResponse(reply="on it", resolved=False)

    ctx = _ctx(dispatcher=lambda a, s: "ok", resolver=lambda t, q: "yes")
    with patch("main_agent.agent.StructuralAgent", side_effect=factory), \
         patch("main_agent.agent.cost_from_agent", return_value=None):
        result = spec.handle(
            TaskMessage(task_id="t1", natural_language="help", ticket_id="TCK"), ctx
        )

    tool_names = {t.name for t in captured["tools"]}
    assert tool_names == {"dispatch", "ask_agent"}
    assert captured["ticket_id"] == "TCK"
    assert result.status == "success"
    assert result.summary == "on it"
    assert result.artifacts["resolved"] is False
    fake.cleanup.assert_called_once()


def test_handle_with_no_seams_has_no_tools():
    spec = MainAgent()
    captured: dict = {}
    fake, factory = _patch_structural(captured)
    fake.invoke.return_value = MainResponse(reply="hello", resolved=True)

    with patch("main_agent.agent.StructuralAgent", side_effect=factory), \
         patch("main_agent.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t1", natural_language="hi"), _ctx())

    assert captured["tools"] == []
    # ticket_id falls back to task_id when the task carries no ticket.
    assert captured["ticket_id"] == "t1"
    assert result.status == "success"
    assert result.artifacts["resolved"] is True


def test_handle_only_dispatch_when_resolver_absent():
    spec = MainAgent()
    captured: dict = {}
    fake, factory = _patch_structural(captured)
    fake.invoke.return_value = MainResponse(reply="x")

    ctx = _ctx(dispatcher=lambda a, s: "ok", resolver=None)
    with patch("main_agent.agent.StructuralAgent", side_effect=factory), \
         patch("main_agent.agent.cost_from_agent", return_value=None):
        spec.handle(TaskMessage(task_id="t", natural_language="q"), ctx)

    assert {t.name for t in captured["tools"]} == {"dispatch"}


def test_handle_exception_returns_failed_with_type_name():
    spec = MainAgent()
    fake = MagicMock()
    fake.invoke.side_effect = RuntimeError("model down")
    with patch("main_agent.agent.StructuralAgent", return_value=fake), \
         patch("main_agent.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="x"), _ctx())

    assert result.status == "failed"
    assert "RuntimeError" in result.summary
    assert "model down" in result.summary
    fake.cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# Conversation continuity within a ticket (regression: agent was stateless
# per turn — couldn't see prior messages, so "yes please!" / "what did I
# ask?" had no context).
# ---------------------------------------------------------------------------


def _seed_ticket(store, ticket_id):
    store.append(TicketEvent(ticket_id=ticket_id, actor="human",
                             kind="human_message", payload={"text": "check slurm health"}))
    store.append(TicketEvent(ticket_id=ticket_id, actor="main",
                             kind="agent_message", payload={"text": "Slurm or GPU?"}))
    store.append(TicketEvent(ticket_id=ticket_id, actor="human",
                             kind="human_message", payload={"text": "yes please!"}))


def test_conversation_history_renders_prior_turns():
    store = InMemoryTicketStore()
    _seed_ticket(store, "TCK")
    # current message == the last human turn → excluded from history
    hist = _conversation_history(store, "TCK", "yes please!")
    assert "human: check slurm health" in hist
    assert "you: Slurm or GPU?" in hist
    # the current "yes please!" must NOT be duplicated into history
    assert hist.count("yes please!") == 0


def test_conversation_history_empty_without_store():
    assert _conversation_history(None, "TCK", "hi") == ""


def test_conversation_history_empty_for_fresh_ticket():
    store = InMemoryTicketStore()
    store.append(TicketEvent(ticket_id="T", actor="human",
                             kind="human_message", payload={"text": "first message"}))
    # Only the current message in the transcript → no prior history.
    assert _conversation_history(store, "T", "first message") == ""


def test_handle_feeds_conversation_history_to_invoke():
    spec = MainAgent()
    captured: dict = {}
    fake, factory = _patch_structural(captured)
    fake.invoke.return_value = MainResponse(reply="checking now", resolved=False)

    store = InMemoryTicketStore()
    _seed_ticket(store, "TCK")
    ctx = _ctx()
    ctx.ticket_store = store
    with patch("main_agent.agent.StructuralAgent", side_effect=factory), \
         patch("main_agent.agent.cost_from_agent", return_value=None):
        spec.handle(
            TaskMessage(task_id="t9", natural_language="yes please!", ticket_id="TCK"), ctx
        )

    sent = fake.invoke.call_args[0][0]
    assert "Conversation so far" in sent
    assert "check slurm health" in sent          # prior turn is visible
    assert "Latest message from the human:\nyes please!" in sent
