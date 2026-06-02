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


# ---------------------------------------------------------------------------
# Streaming path: when a token_sink is wired the coordinator emits a stream of
# JSON objects that _StreamParser turns into interleaved "thinking" transcript
# events + a clean reply. No raw JSON ever reaches the UI (the old bug).
# ---------------------------------------------------------------------------


class _FakeStreamingAgent:
    """Stand-in for StreamingAgent: replays a scripted JSONL stream."""

    def __init__(self, script, **kwargs):
        self._script = script
        self.cleaned = False

    def stream(self, _message):
        for chunk in self._script:
            yield chunk

    def total_token_counts(self):
        return (12, 7)

    def total_cost_breakdown(self):
        return (0.0009, {"input": 0.0, "output": 0.0009})

    def cleanup(self):
        self.cleaned = True


def _stream_factory(script):
    holder = {}

    def factory(*args, **kwargs):
        agent = _FakeStreamingAgent(script, **kwargs)
        holder["agent"] = agent
        return agent

    return holder, factory


def test_streaming_emits_interleaved_thinking_and_clean_reply():
    spec = MainAgent()
    store = InMemoryTicketStore()
    pushed: list[tuple[str, str]] = []

    ctx = _ctx()
    ctx.ticket_store = store
    ctx.token_sink = lambda tid, chunk: pushed.append((tid, chunk))

    script = [
        '{"thinking": "I should ask sysadmin for the node list"}\n',
        '{"thinking": "sysad',  # a thinking step split across two chunks
        'min reported 2 nodes"}\n',
        '{"reply": "There are 2 nodes: cp and w1.", "resolved": true}',
    ]
    holder, factory = _stream_factory(script)
    with patch("main_agent.agent.StreamingAgent", factory):
        result = spec.handle(
            TaskMessage(task_id="t1", natural_language="what nodes?", ticket_id="TCK"),
            ctx,
        )

    # Reply is the parsed prose only — no JSON, no braces.
    assert result.status == "success"
    assert result.summary == "There are 2 nodes: cp and w1."
    assert result.artifacts["resolved"] is True
    assert "{" not in result.summary and "thinking" not in result.summary

    # Each thinking step landed as an interleaved, persisted transcript event.
    thinking = [e for e in store.transcript("TCK") if e.kind == "agent_thinking"]
    assert [e.payload["text"] for e in thinking] == [
        "I should ask sysadmin for the node list",
        "sysadmin reported 2 nodes",
    ]
    assert all(e.actor == "main" for e in thinking)

    # The reply was pushed once to the live token sink, and the agent cleaned up.
    assert pushed == [("TCK", "There are 2 nodes: cp and w1.")]
    assert holder["agent"].cleaned is True


def test_streaming_falls_back_to_structural_without_token_sink():
    """No token_sink → the structured one-shot path (tests/non-chat callers)."""
    spec = MainAgent()
    captured: dict = {}
    fake, factory = _patch_structural(captured)
    fake.invoke.return_value = MainResponse(reply="structured", resolved=False)

    # StreamingAgent must NOT be constructed on this path.
    with patch("main_agent.agent.StructuralAgent", side_effect=factory), \
         patch("main_agent.agent.StreamingAgent", side_effect=AssertionError("should not stream")), \
         patch("main_agent.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="hi"), _ctx())

    assert result.summary == "structured"


def test_streaming_handles_agent_exception():
    spec = MainAgent()
    ctx = _ctx()
    ctx.ticket_store = InMemoryTicketStore()
    ctx.token_sink = lambda tid, chunk: None

    class _Boom:
        def __init__(self, **kwargs):
            pass

        def stream(self, _m):
            raise RuntimeError("stream died")
            yield  # pragma: no cover - makes this a generator

        def total_token_counts(self):
            return (0, 0)

        def total_cost_breakdown(self):
            return (0.0, {})

        def cleanup(self):
            pass

    with patch("main_agent.agent.StreamingAgent", _Boom):
        result = spec.handle(
            TaskMessage(task_id="t", natural_language="x", ticket_id="TCK"), ctx
        )

    assert result.status == "failed"
    assert "RuntimeError" in result.summary
