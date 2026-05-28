"""
Tests for agentlib.ticket — the group-chat ticket spine.

Pure-python, no LLM: the store, the bus→ticket projection, the ask_agent
tool (with an injected fake resolver), and the gate_tools wiring that emits
``tool_call`` events and injects ``ask_agent``.
"""
from __future__ import annotations

from typing import Any, Sequence

from langchain_core.tools import tool

from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    AlwaysApprove,
    AlwaysReject,
    InMemoryAuditLogger,
    InMemoryBus,
    InMemoryTicketStore,
    JsonlTicketStore,
    TaskMessage,
    TicketEvent,
    event_from_bus,
    gate_tools,
    make_ask_agent_tool,
    new_message,
    ticket_bus_sink,
)

# ---------------------------------------------------------------------------
# Store: seq, transcript, isolation
# ---------------------------------------------------------------------------

def _ev(ticket_id: str, actor: str = "main", kind: str = "agent_message", payload: Any = "hi") -> TicketEvent:
    return TicketEvent(ticket_id=ticket_id, actor=actor, kind=kind, payload=payload)


def test_inmemory_append_assigns_monotonic_seq_per_ticket():
    store = InMemoryTicketStore()
    a = store.append(_ev("T1"))
    b = store.append(_ev("T1"))
    c = store.append(_ev("T1"))
    assert [a.seq, b.seq, c.seq] == [1, 2, 3]


def test_inmemory_transcript_ordering_and_after_seq():
    store = InMemoryTicketStore()
    for i in range(5):
        store.append(_ev("T1", payload=f"m{i}"))

    full = store.transcript("T1")
    assert [e.seq for e in full] == [1, 2, 3, 4, 5]
    assert [e.payload for e in full] == ["m0", "m1", "m2", "m3", "m4"]

    tail = store.transcript("T1", after_seq=3)
    assert [e.seq for e in tail] == [4, 5]


def test_inmemory_tickets_have_independent_seq_spaces():
    store = InMemoryTicketStore()
    store.append(_ev("T1"))
    store.append(_ev("T2"))
    store.append(_ev("T1"))
    assert [e.seq for e in store.transcript("T1")] == [1, 2]
    assert [e.seq for e in store.transcript("T2")] == [1]
    assert store.transcript("nope") == []


# ---------------------------------------------------------------------------
# JSONL store: persistence, reload-continues-seq, filename safety
# ---------------------------------------------------------------------------

def test_jsonl_persists_and_reads_back(tmp_path):
    store = JsonlTicketStore(tmp_path)
    store.append(_ev("T1", actor="human", kind="human_message", payload="hello"))
    store.append(_ev("T1", actor="sysadmin", kind="agent_message", payload={"k": "v"}))

    out = store.transcript("T1")
    assert [e.seq for e in out] == [1, 2]
    assert out[0].actor == "human"
    assert out[1].payload == {"k": "v"}


def test_jsonl_seq_continues_across_new_store_instance(tmp_path):
    s1 = JsonlTicketStore(tmp_path)
    s1.append(_ev("T1"))
    s1.append(_ev("T1"))

    # Fresh instance over the same dir must keep counting, not restart at 1.
    s2 = JsonlTicketStore(tmp_path)
    third = s2.append(_ev("T1"))
    assert third.seq == 3
    assert [e.seq for e in s2.transcript("T1")] == [1, 2, 3]


def test_jsonl_sanitizes_ticket_id_with_colons(tmp_path):
    # ticket_id falls back to task_id, which contains ':' — must not escape
    # the tickets dir or crash.
    store = JsonlTicketStore(tmp_path)
    store.append(_ev("task-123:sysadmin:default"))
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert ":" not in files[0].name
    assert [e.seq for e in store.transcript("task-123:sysadmin:default")] == [1]


def test_inmemory_list_tickets_summaries_recent_first():
    store = InMemoryTicketStore()
    store.append(TicketEvent(ticket_id="T1", actor="human", kind="human_message", payload={"text": "first ticket"}))
    store.append(TicketEvent(ticket_id="T1", actor="main", kind="agent_message", payload={"text": "reply"}))
    store.append(TicketEvent(ticket_id="T2", actor="human", kind="human_message", payload={"text": "second ticket"}))

    tickets = store.list_tickets()
    assert {t["ticket_id"] for t in tickets} == {"T1", "T2"}
    t1 = next(t for t in tickets if t["ticket_id"] == "T1")
    assert t1["event_count"] == 2
    assert t1["first_message"] == "first ticket"
    assert t1["last_actor"] == "main"
    # Most-recently-active first (T2 appended last).
    assert tickets[0]["ticket_id"] == "T2"


def test_jsonl_list_tickets_reads_all_files(tmp_path):
    store = JsonlTicketStore(tmp_path)
    store.append(TicketEvent(ticket_id="task-a:x", actor="human", kind="human_message", payload={"text": "hi a"}))
    store.append(TicketEvent(ticket_id="T-b", actor="human", kind="human_message", payload={"text": "hi b"}))
    tickets = store.list_tickets()
    assert {t["ticket_id"] for t in tickets} == {"task-a:x", "T-b"}
    assert all(t["first_message"].startswith("hi") for t in tickets)


def test_jsonl_after_seq_filter(tmp_path):
    store = JsonlTicketStore(tmp_path)
    for i in range(4):
        store.append(_ev("T1", payload=f"m{i}"))
    tail = store.transcript("T1", after_seq=2)
    assert [e.seq for e in tail] == [3, 4]


# ---------------------------------------------------------------------------
# Bus → ticket projection
# ---------------------------------------------------------------------------

def test_event_from_bus_maps_known_kinds():
    msg = new_message("task-1", "orchestrator", "sysadmin", "task", {"x": 1})
    ev = event_from_bus(msg)
    assert ev is not None
    assert ev.kind == "dispatch"
    assert ev.actor == "orchestrator"
    assert ev.ticket_id == "task-1"  # falls back to task_id
    assert ev.task_id == "task-1"

    result_msg = new_message("task-1", "sysadmin", "orchestrator", "result", "done")
    assert event_from_bus(result_msg).kind == "agent_result"


def test_event_from_bus_uses_explicit_ticket_id():
    msg = new_message("task-1", "main", "sysadmin", "task", {}, ticket_id="TICKET-9")
    ev = event_from_bus(msg)
    assert ev.ticket_id == "TICKET-9"
    assert ev.task_id == "task-1"


def test_event_from_bus_drops_unmapped_kind():
    msg = new_message("task-1", "sysadmin", "*", "log", "noise")
    assert event_from_bus(msg) is None


def test_ticket_bus_sink_records_projectable_traffic():
    bus = InMemoryBus()
    store = InMemoryTicketStore()
    bus.subscribe("*", ticket_bus_sink(store))

    bus.publish(new_message("T1", "orchestrator", "sysadmin", "task", {"do": "x"}))
    bus.publish(new_message("T1", "sysadmin", "*", "log", "ignored"))
    bus.publish(new_message("T1", "sysadmin", "orchestrator", "result", "ok"))

    out = store.transcript("T1")
    assert [e.kind for e in out] == ["dispatch", "agent_result"]
    assert [e.seq for e in out] == [1, 2]


# ---------------------------------------------------------------------------
# ask_agent tool
# ---------------------------------------------------------------------------

def test_ask_agent_round_trip_logs_question_and_answer():
    store = InMemoryTicketStore()
    calls: list[tuple[str, str]] = []

    def resolver(target: str, question: str) -> str:
        calls.append((target, question))
        return f"{target} says: it's fine"

    tool_ = make_ask_agent_tool(
        asker="programmer", resolver=resolver, ticket_id="T1", ticket_store=store
    )
    answer = tool_.invoke({"target_agent": "sysadmin", "question": "is the pod up?"})

    assert answer == "sysadmin says: it's fine"
    assert calls == [("sysadmin", "is the pod up?")]

    # Both the question (from asker) and the answer (from target) are logged.
    out = store.transcript("T1")
    assert [e.kind for e in out] == ["agent_message", "agent_message"]
    assert out[0].actor == "programmer"
    assert out[0].payload["type"] == "question"
    assert out[1].actor == "sysadmin"
    assert out[1].payload["type"] == "answer"
    assert out[1].payload["answer"] == "sysadmin says: it's fine"


def test_ask_agent_isolates_target_context():
    """The asker sees only the target's answer string — never its raw
    context. We assert the only thing returned/logged is the answer."""
    store = InMemoryTicketStore()

    def resolver(target: str, question: str) -> str:
        # Pretend the target reasoned over a big private context internally.
        secret_context = "PRIVATE: 40 internal tool results ..."
        assert secret_context  # used internally, never returned
        return "summary: all green"

    tool_ = make_ask_agent_tool(
        asker="a", resolver=resolver, ticket_id="T1", ticket_store=store
    )
    answer = tool_.invoke({"target_agent": "b", "question": "status?"})
    assert answer == "summary: all green"
    assert "PRIVATE" not in str(store.transcript("T1"))


def test_ask_agent_swallows_resolver_error():
    store = InMemoryTicketStore()

    def boom(target: str, question: str) -> str:
        raise RuntimeError("target unavailable")

    tool_ = make_ask_agent_tool(
        asker="a", resolver=boom, ticket_id="T1", ticket_store=store
    )
    answer = tool_.invoke({"target_agent": "b", "question": "?"})
    assert "ask_agent error" in answer
    assert "RuntimeError" in answer
    # Question + error-answer both still recorded.
    assert len(store.transcript("T1")) == 2


def test_ask_agent_works_without_store():
    tool_ = make_ask_agent_tool(
        asker="a", resolver=lambda t, q: "ok", ticket_id="T1"
    )
    assert tool_.invoke({"target_agent": "b", "question": "?"}) == "ok"


# ---------------------------------------------------------------------------
# gate_tools wiring: tool_call emission + ask_agent injection
# ---------------------------------------------------------------------------

@tool
def safe_read(name: str) -> str:
    """Read something harmless."""
    return f"read:{name}"


@tool
def dangerous_delete(target: str) -> str:
    """Pretend to delete something."""
    return f"deleted:{target}"


class _StubAgent(AgentSpec):
    name = "stub"
    domain = "test"
    tools: Sequence[Any] = [safe_read, dangerous_delete]
    destructive_verbs = {"dangerous_delete"}

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        raise NotImplementedError


def test_gate_tools_emits_tool_call_event_for_read():
    store = InMemoryTicketStore()
    ctx = AgentContext(
        approval=AlwaysApprove(), audit=InMemoryAuditLogger(), ticket_store=store
    )
    gated = gate_tools(_StubAgent(), ctx, task_id="task-1", ticket_id="T1")
    by_name = {t.name: t for t in gated}

    by_name["safe_read"].invoke({"name": "pod-a"})

    out = store.transcript("T1")
    assert len(out) == 1
    assert out[0].kind == "tool_call"
    assert out[0].actor == "stub"
    assert out[0].payload["tool"] == "safe_read"
    assert out[0].payload["result"] == "read:pod-a"
    assert out[0].payload["approved"] is None
    assert out[0].task_id == "task-1"


def test_gate_tools_emits_tool_call_for_destructive_approved_and_rejected():
    # Approved path.
    store = InMemoryTicketStore()
    ctx = AgentContext(
        approval=AlwaysApprove(), audit=InMemoryAuditLogger(), ticket_store=store
    )
    gated = {t.name: t for t in gate_tools(_StubAgent(), ctx, "task-1", "T1")}
    gated["dangerous_delete"].invoke({"target": "pod-x"})
    approved = store.transcript("T1")
    assert len(approved) == 1
    assert approved[0].payload["approved"] is True
    assert approved[0].payload["result"] == "deleted:pod-x"

    # Rejected path — one tool_call event, no execution result.
    store2 = InMemoryTicketStore()
    ctx2 = AgentContext(
        approval=AlwaysReject(), audit=InMemoryAuditLogger(), ticket_store=store2
    )
    gated2 = {t.name: t for t in gate_tools(_StubAgent(), ctx2, "task-1", "T1")}
    result = gated2["dangerous_delete"].invoke({"target": "pod-y"})
    assert "REJECTED" in result
    rejected = store2.transcript("T1")
    assert len(rejected) == 1
    assert rejected[0].payload["approved"] is False
    assert rejected[0].payload["result"] is None


def test_gate_tools_injects_ask_agent_when_resolver_present():
    store = InMemoryTicketStore()
    seen: list[tuple[str, str]] = []

    def resolver(target: str, question: str) -> str:
        seen.append((target, question))
        return "answer-from-target"

    ctx = AgentContext(
        approval=AlwaysApprove(),
        audit=InMemoryAuditLogger(),
        ticket_store=store,
        agent_resolver=resolver,
    )
    gated = gate_tools(_StubAgent(), ctx, task_id="task-1", ticket_id="T1")
    by_name = {t.name: t for t in gated}

    assert "ask_agent" in by_name
    answer = by_name["ask_agent"].invoke(
        {"target_agent": "sysadmin", "question": "up?"}
    )
    assert answer == "answer-from-target"
    assert seen == [("sysadmin", "up?")]
    # Asker is the gating agent's name.
    out = store.transcript("T1")
    assert out[0].actor == "stub"
    assert out[1].actor == "sysadmin"


def test_gate_tools_no_ticket_wiring_is_backward_compatible():
    """With neither ticket_store nor agent_resolver, behavior is unchanged:
    no ask_agent tool, no crash, tools still run."""
    ctx = AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())
    gated = gate_tools(_StubAgent(), ctx, task_id="task-1")
    by_name = {t.name: t for t in gated}

    assert "ask_agent" not in by_name
    assert by_name["safe_read"].invoke({"name": "z"}) == "read:z"


def test_gate_tools_ticket_id_defaults_to_task_id():
    store = InMemoryTicketStore()
    ctx = AgentContext(
        approval=AlwaysApprove(), audit=InMemoryAuditLogger(), ticket_store=store
    )
    # No ticket_id passed → falls back to task_id as the ticket scope.
    gated = {t.name: t for t in gate_tools(_StubAgent(), ctx, task_id="task-9")}
    gated["safe_read"].invoke({"name": "p"})
    assert [e.seq for e in store.transcript("task-9")] == [1]
