"""
Phase 2 ticket dispatch — Orchestrator.dispatch_to, the per-ticket
ask_agent resolver + dispatcher seams, retained per-(ticket, agent)
checkpointers, and the recursion guard.

No LLM: fake AgentSpecs read the ctx the orchestrator injects and drive
the seams directly. A counter-based checkpointer_factory lets us assert
retention/isolation without langgraph.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

import pytest

from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    AlwaysApprove,
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    InMemoryMemoryStore,
    InMemoryTicketStore,
    ManualRouter,
    Orchestrator,
    TaskMessage,
    TicketEvent,
)


class _SpyAgent(AgentSpec):
    """Records the ctx + task it received; summary is either canned or
    produced by an injected behavior(task, ctx) callable (so a fake agent
    can exercise ctx.dispatcher / ctx.agent_resolver)."""

    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()

    def __init__(self, name: str, behavior: Optional[Callable[[TaskMessage, AgentContext], str]] = None):
        self.name = name
        self.domain = "spy"
        self.seen_ctx: list[AgentContext] = []
        self.seen_tasks: list[TaskMessage] = []
        self._behavior = behavior

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.seen_ctx.append(ctx)
        self.seen_tasks.append(task)
        summary = (
            self._behavior(task, ctx)
            if self._behavior is not None
            else f"{self.name}:{task.natural_language}"
        )
        return AgentResult(
            task_id=task.task_id, status="success", summary=summary, cost=CostBreakdown()
        )


def _ctx() -> AgentContext:
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def _counter_factory() -> Callable[[], str]:
    n = {"i": 0}

    def factory() -> str:
        n["i"] += 1
        return f"cp-{n['i']}"

    return factory


def _orch(agents, store=None, factory=None) -> Orchestrator:
    return Orchestrator(
        bus=InMemoryBus(),
        agents=agents,
        ctx=_ctx(),
        router=ManualRouter(default=agents[0].name),
        ticket_store=store or InMemoryTicketStore(),
        checkpointer_factory=factory or _counter_factory(),
    )


def _task(nl: str, ticket: str) -> TaskMessage:
    return TaskMessage(task_id=f"task-{nl[:4]}", natural_language=nl, ticket_id=ticket)


# ---------------------------------------------------------------------------

def test_dispatch_to_runs_agent_and_records_transcript():
    store = InMemoryTicketStore()
    worker = _SpyAgent("worker")
    orch = _orch([worker], store=store)

    result = orch.dispatch_to("worker", _task("do x", "T1"))

    assert result.status == "success"
    assert result.summary == "worker:do x"
    assert len(worker.seen_tasks) == 1

    # Transcript captured a dispatch (from the requester) + an agent_result.
    evs = store.transcript("T1")
    assert [e.kind for e in evs] == ["dispatch", "agent_result"]
    assert evs[0].actor == "main"
    assert evs[0].payload["to"] == "worker"
    assert evs[1].actor == "worker"
    assert evs[1].payload["status"] == "success"


def test_announce_false_runs_without_transcript_events():
    store = InMemoryTicketStore()
    worker = _SpyAgent("worker")
    orch = _orch([worker], store=store)

    result = orch.dispatch_to("worker", _task("quiet", "T1"), announce=False)

    assert result.status == "success"
    assert store.transcript("T1") == []


def test_ticket_ctx_carries_seams_and_checkpointer():
    worker = _SpyAgent("worker")
    factory = _counter_factory()
    orch = _orch([worker], factory=factory)

    orch.dispatch_to("worker", _task("go", "T1"))
    ctx = worker.seen_ctx[0]

    assert ctx.ticket_store is orch.ticket_store
    assert callable(ctx.agent_resolver)
    assert callable(ctx.dispatcher)
    assert ctx.checkpointer == "cp-1"  # from the counter factory


def test_checkpointer_retained_per_ticket_agent_and_isolated_across_tickets():
    worker = _SpyAgent("worker")
    orch = _orch([worker], factory=_counter_factory())

    orch.dispatch_to("worker", _task("a", "T1"))
    orch.dispatch_to("worker", _task("b", "T1"))  # same (ticket, agent)
    orch.dispatch_to("worker", _task("c", "T2"))  # different ticket

    cps = [c.checkpointer for c in worker.seen_ctx]
    assert cps[0] == cps[1]          # retained within a ticket
    assert cps[0] != cps[2]          # isolated across tickets


def test_resolver_round_trip_b_answers_a():
    # Agent A, when run, asks B a question via the injected resolver and
    # returns B's answer as its own summary.
    def a_behavior(task: TaskMessage, ctx: AgentContext) -> str:
        return ctx.agent_resolver("b", "are you ok?")

    a = _SpyAgent("a", behavior=a_behavior)
    b = _SpyAgent("b", behavior=lambda t, c: "b says: all good")
    orch = _orch([a, b])

    result = orch.dispatch_to("a", _task("start", "T1"))

    assert result.summary == "b says: all good"
    # B actually ran (received the question as its task).
    assert b.seen_tasks[0].natural_language == "are you ok?"
    assert b.seen_tasks[0].ticket_id == "T1"


def test_dispatcher_seam_delegates_to_named_agent():
    def main_behavior(task: TaskMessage, ctx: AgentContext) -> str:
        return "delegated -> " + ctx.dispatcher("worker", "subtask!")

    main = _SpyAgent("main", behavior=main_behavior)
    worker = _SpyAgent("worker")
    orch = _orch([main, worker])

    result = orch.dispatch_to("main", _task("coordinate", "T1"))

    assert result.summary == "delegated -> worker:subtask!"
    assert worker.seen_tasks[0].natural_language == "subtask!"


def test_recursion_guard_refuses_reentrant_self_invocation():
    # Agent A asks itself — must be refused, not recurse forever.
    def a_behavior(task: TaskMessage, ctx: AgentContext) -> str:
        return ctx.agent_resolver("a", "loop?")

    a = _SpyAgent("a", behavior=a_behavior)
    orch = _orch([a])

    result = orch.dispatch_to("a", _task("start", "T1"))

    assert "recursion guard" in result.summary
    # A's outer run happened once; the re-entrant call was refused (no
    # second full execution recorded a new context append).
    assert len(a.seen_tasks) == 1


def test_resolver_unknown_agent_returns_error_string():
    a = _SpyAgent("a", behavior=lambda t, c: c.agent_resolver("ghost", "?"))
    orch = _orch([a])
    result = orch.dispatch_to("a", _task("start", "T1"))
    assert "unknown agent 'ghost'" in result.summary


def test_dispatch_to_unknown_agent_raises():
    orch = _orch([_SpyAgent("a")])
    with pytest.raises(ValueError, match="unknown agent"):
        orch.dispatch_to("nope", _task("x", "T1"))


def test_discard_ticket_drops_checkpointers():
    worker = _SpyAgent("worker")
    orch = _orch([worker], factory=_counter_factory())
    orch.dispatch_to("worker", _task("a", "T1"))
    assert ("T1", "worker") in orch._ticket_checkpointers

    orch.discard_ticket("T1")
    assert ("T1", "worker") not in orch._ticket_checkpointers


def test_router_path_unaffected_no_ticket_ctx():
    # The plain router path (run) must not set ticket seams on the ctx.
    worker = _SpyAgent("worker")
    orch = _orch([worker])
    orch.run(TaskMessage(task_id="R1", natural_language="route me"))
    ctx = worker.seen_ctx[0]
    assert ctx.checkpointer is None
    assert ctx.dispatcher is None


# ---- Phase 3: close -> summarize -> memory ----

def _orch_with_memory(agents, memory, store=None):
    return Orchestrator(
        bus=InMemoryBus(),
        agents=agents,
        ctx=_ctx(),
        router=ManualRouter(default=agents[0].name),
        memory=memory,
        ticket_store=store or InMemoryTicketStore(),
        checkpointer_factory=_counter_factory(),
    )


def test_close_ticket_writes_summary_to_memory_and_discards_checkpoints():
    store = InMemoryTicketStore()
    memory = InMemoryMemoryStore()
    orch = _orch_with_memory([_SpyAgent("main"), _SpyAgent("worker")], memory, store)

    # Build a small transcript: human asks, main dispatches worker, replies.
    store.append(TicketEvent(ticket_id="T1", actor="human", kind="human_message", payload={"text": "check the cluster"}))
    orch.dispatch_to("worker", _task("inspect", "T1"))  # dispatch + agent_result
    store.append(TicketEvent(ticket_id="T1", actor="main", kind="agent_message", payload={"text": "all healthy"}))
    assert ("T1", "worker") in orch._ticket_checkpointers

    entry = orch.close_ticket("T1")

    assert entry is not None
    assert entry.task_id == "T1"
    assert entry.agent == "main"
    assert entry.natural_language == "check the cluster"
    assert "check the cluster" in entry.summary
    assert "all healthy" in entry.summary
    assert "worker" in entry.summary  # specialist recorded
    # Memory now holds it, checkpoints gone.
    assert len(memory.search("cluster", k=5, agent="main")) >= 1
    assert ("T1", "worker") not in orch._ticket_checkpointers


def test_close_ticket_accepts_custom_summarizer():
    store = InMemoryTicketStore()
    memory = InMemoryMemoryStore()
    orch = _orch_with_memory([_SpyAgent("a")], memory, store)
    store.append(TicketEvent(ticket_id="T1", actor="human", kind="human_message", payload={"text": "q"}))

    entry = orch.close_ticket("T1", summarizer=lambda events: f"custom:{len(events)} events")
    assert entry.summary == "custom:1 events"


def test_close_ticket_no_memory_returns_none_but_discards():
    store = InMemoryTicketStore()
    orch = _orch([_SpyAgent("worker")], store=store)  # NullMemoryStore default
    orch.dispatch_to("worker", _task("x", "T1"))
    assert ("T1", "worker") in orch._ticket_checkpointers
    entry = orch.close_ticket("T1")
    assert entry is None
    assert ("T1", "worker") not in orch._ticket_checkpointers


def test_close_ticket_without_ticket_store_is_noop():
    orch = Orchestrator(
        bus=InMemoryBus(), agents=[_SpyAgent("a")], ctx=_ctx(),
        router=ManualRouter(default="a"),
    )
    assert orch.close_ticket("T1") is None


def test_dispatch_with_memory_prepends_prior_context():
    store = InMemoryTicketStore()
    memory = InMemoryMemoryStore()
    # Seed a closed ticket's memory.
    orch = _orch_with_memory([_SpyAgent("main")], memory, store)
    store.append(TicketEvent(ticket_id="OLD", actor="human", kind="human_message", payload={"text": "disk space on nodes"}))
    store.append(TicketEvent(ticket_id="OLD", actor="main", kind="agent_message", payload={"text": "node-3 at 92%"}))
    orch.close_ticket("OLD")

    # A new main turn with_memory should see the prior context prepended.
    main = orch.agents["main"]
    orch.dispatch_to("main", _task("disk space again", "NEW"), announce=False, with_memory=True)
    seen_nl = main.seen_tasks[-1].natural_language
    assert "disk space again" in seen_nl
    assert "node-3 at 92%" in seen_nl  # prior outcome retrieved from memory
