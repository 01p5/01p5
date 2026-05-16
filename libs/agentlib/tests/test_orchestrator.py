"""
Tests for the in-memory bus + orchestrator routing.

Uses stub AgentSpec implementations so the routing path runs without
an LLM. The LLMRouter is exercised in integration tests with credentials.
"""
from __future__ import annotations

from typing import Any, Sequence

from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    AlwaysApprove,
    BusMessage,
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    InMemoryMemoryStore,
    JsonlMemoryStore,
    ManualRouter,
    MemoryEntry,
    NullMemoryStore,
    Orchestrator,
    TaskMessage,
)


class _EchoAgent(AgentSpec):
    """Records every task it receives and returns a deterministic result."""
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()

    def __init__(self, name: str, domain: str = "test domain"):
        self.name = name
        self.domain = domain
        self.received: list[TaskMessage] = []

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.received.append(task)
        return AgentResult(
            task_id=task.task_id,
            status="success",
            summary=f"{self.name} handled {task.natural_language!r}",
            artifacts={"handled_by": self.name},
            cost=CostBreakdown(wall_seconds=0.0),
        )


def _ctx() -> AgentContext:
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_manual_router_dispatches_default():
    bus = InMemoryBus()
    a = _EchoAgent("alpha")
    b = _EchoAgent("beta")
    orch = Orchestrator(
        bus=bus,
        agents=[a, b],
        ctx=_ctx(),
        router=ManualRouter(default="alpha"),
    )

    result = orch.run(TaskMessage(task_id="T1", natural_language="anything"))

    assert result.status == "success"
    assert result.artifacts["handled_by"] == "alpha"
    assert len(a.received) == 1
    assert len(b.received) == 0


def test_manual_router_keyword_routing():
    bus = InMemoryBus()
    sysadmin = _EchoAgent("sysadmin")
    programmer = _EchoAgent("programmer")
    orch = Orchestrator(
        bus=bus,
        agents=[sysadmin, programmer],
        ctx=_ctx(),
        router=ManualRouter(
            default="sysadmin",
            by_keyword={"dockerfile": "programmer", "helm": "programmer"},
        ),
    )

    r1 = orch.run(TaskMessage(task_id="T1", natural_language="generate a Dockerfile for python"))
    r2 = orch.run(TaskMessage(task_id="T2", natural_language="why is my pod crashlooping"))

    assert r1.artifacts["handled_by"] == "programmer"
    assert r2.artifacts["handled_by"] == "sysadmin"


def test_bus_log_captures_full_roundtrip():
    bus = InMemoryBus()
    agent = _EchoAgent("alpha")
    orch = Orchestrator(
        bus=bus,
        agents=[agent],
        ctx=_ctx(),
        router=ManualRouter(default="alpha"),
    )

    orch.run(TaskMessage(task_id="T1", natural_language="hello"))

    log = bus.log
    assert len(log) == 2
    # Task message: orchestrator → alpha
    assert log[0].kind == "task"
    assert log[0].sender == "orchestrator"
    assert log[0].recipient == "alpha"
    # Result message: alpha → orchestrator, with causation pointing back
    assert log[1].kind == "result"
    assert log[1].sender == "alpha"
    assert log[1].recipient == "orchestrator"
    assert log[1].causation_id == log[0].msg_id


def test_unknown_agent_in_router_raises():
    bus = InMemoryBus()
    a = _EchoAgent("alpha")
    orch = Orchestrator(
        bus=bus,
        agents=[a],
        ctx=_ctx(),
        router=ManualRouter(default="ghost"),
    )

    try:
        orch.run(TaskMessage(task_id="T1", natural_language="x"))
    except ValueError as e:
        assert "ghost" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown agent")


def test_broadcast_subscriber_sees_all_messages():
    bus = InMemoryBus()
    agent = _EchoAgent("alpha")
    seen: list[BusMessage] = []
    bus.subscribe("*", seen.append)
    orch = Orchestrator(
        bus=bus,
        agents=[agent],
        ctx=_ctx(),
        router=ManualRouter(default="alpha"),
    )

    orch.run(TaskMessage(task_id="T1", natural_language="hello"))

    # Broadcast subscriber should see both task and result.
    kinds = [m.kind for m in seen]
    assert "task" in kinds
    assert "result" in kinds


# ---------------------------------------------------------------------------
# Memory integration
# ---------------------------------------------------------------------------


def _orch_with_memory(memory):
    bus = InMemoryBus()
    agent = _EchoAgent("alpha")
    orch = Orchestrator(
        bus=bus,
        agents=[agent],
        ctx=_ctx(),
        router=ManualRouter(default="alpha"),
        memory=memory,
    )
    return orch, agent


def test_orchestrator_default_memory_is_null():
    """No memory= passed → behaviour is identical to the v1 orchestrator
    (no prepended context, no writes happening)."""
    bus = InMemoryBus()
    agent = _EchoAgent("alpha")
    orch = Orchestrator(
        bus=bus, agents=[agent], ctx=_ctx(),
        router=ManualRouter(default="alpha"),
    )
    assert isinstance(orch.memory, NullMemoryStore)


def test_memory_write_after_success():
    mem = InMemoryMemoryStore()
    orch, _ = _orch_with_memory(mem)
    orch.run(TaskMessage(task_id="T1", natural_language="delete pod nginx in default"))

    hits = mem.search("delete pod nginx", k=3)
    assert len(hits) == 1
    assert hits[0].task_id == "T1"
    assert hits[0].agent == "alpha"
    assert hits[0].status == "success"
    assert hits[0].summary.startswith("alpha handled")


def test_second_task_sees_first_task_in_prompt():
    """The agent's view of natural_language on the second task must
    include the first task's summary as prepended context."""
    mem = InMemoryMemoryStore()
    orch, agent = _orch_with_memory(mem)

    orch.run(TaskMessage(task_id="T1", natural_language="delete pod web in default"))
    orch.run(TaskMessage(task_id="T2", natural_language="delete pod api in default"))

    # The agent received T2 with the T1 outcome threaded in.
    assert len(agent.received) == 2
    t2_prompt = agent.received[1].natural_language
    assert "delete pod web in default" in t2_prompt  # T1 NL surfaces
    assert "alpha handled" in t2_prompt              # T1 summary surfaces
    assert "untrusted" in t2_prompt.lower()          # safety prefix is present
    assert "---" in t2_prompt                        # separator before real task


def test_memory_filters_by_routed_agent():
    """A task routed to 'alpha' must not pull context from 'beta'
    runs — cross-agent retrieval is intentionally off in v1."""
    mem = InMemoryMemoryStore()
    bus = InMemoryBus()
    alpha = _EchoAgent("alpha")
    beta = _EchoAgent("beta")
    orch = Orchestrator(
        bus=bus, agents=[alpha, beta], ctx=_ctx(),
        router=ManualRouter(
            default="alpha",
            by_keyword={"terraform": "beta"},
        ),
        memory=mem,
    )

    orch.run(TaskMessage(task_id="T1", natural_language="run terraform plan in pve"))
    orch.run(TaskMessage(task_id="T2", natural_language="run terraform plan again"))

    # Both terraform tasks routed to beta — second sees first.
    assert "T1" in beta.received[1].natural_language or \
        "run terraform plan in pve" in beta.received[1].natural_language

    # Now send an alpha task whose tokens overlap heavily with the
    # terraform tasks but should NOT pick up beta's history.
    orch.run(TaskMessage(task_id="T3", natural_language="run terraform plan"))
    # Routing: "terraform" keyword → beta, so this one also goes to
    # beta. We need a query that lands on alpha — use a token-rich
    # request without the keyword.
    orch.run(TaskMessage(task_id="T4", natural_language="run plan in pve namespace"))
    t4_prompt = alpha.received[-1].natural_language
    assert "run plan in pve namespace" in t4_prompt
    # No leakage from beta runs.
    assert "alpha handled" not in t4_prompt or "T1" not in t4_prompt
    # The block-prefix only shows if the memory actually had alpha
    # entries — for T4 (alpha's first task) there are none, so the
    # prompt is the raw NL.
    assert "untrusted" not in t4_prompt.lower()


def test_memory_write_strips_prepended_context_from_stored_nl():
    """The stored natural_language must be the user's original text,
    not the augmented prompt — otherwise each successive run pollutes
    its own retrieval bucket."""
    mem = InMemoryMemoryStore()
    orch, _ = _orch_with_memory(mem)

    orch.run(TaskMessage(task_id="T1", natural_language="delete pod alpha"))
    orch.run(TaskMessage(task_id="T2", natural_language="delete pod beta"))

    # All stored entries' NL must equal the user's original input,
    # NOT contain the prepended block / separator.
    for entry in [mem.search("delete", k=10, agent="alpha")][0]:
        assert "untrusted" not in entry.natural_language.lower()
        assert "---" not in entry.natural_language


def test_memory_write_failure_does_not_break_run(tmp_path):
    """A misbehaving memory store must not turn into a task failure."""

    class _BadStore:
        def write(self, entry: MemoryEntry) -> None:
            raise RuntimeError("disk full")

        def search(self, query, k=3, agent=None):
            return []

    bus = InMemoryBus()
    agent = _EchoAgent("alpha")
    orch = Orchestrator(
        bus=bus, agents=[agent], ctx=_ctx(),
        router=ManualRouter(default="alpha"),
        memory=_BadStore(),
    )

    result = orch.run(TaskMessage(task_id="T1", natural_language="hello"))
    assert result.status == "success"


def test_jsonl_memory_round_trips_through_orchestrator(tmp_path):
    """End-to-end with the disk-backed store: T1 in process A, then
    T2 in process B (simulated by a fresh JsonlMemoryStore on the
    same path) still sees T1's outcome threaded into T2's prompt."""
    path = tmp_path / "olympus-memory.jsonl"

    orch_a, _ = _orch_with_memory(JsonlMemoryStore(path))
    orch_a.run(TaskMessage(task_id="T1", natural_language="check pod web logs"))

    orch_b, agent_b = _orch_with_memory(JsonlMemoryStore(path))
    orch_b.run(TaskMessage(task_id="T2", natural_language="check pod api logs"))

    t2_prompt = agent_b.received[0].natural_language
    assert "check pod web logs" in t2_prompt
    assert "untrusted" in t2_prompt.lower()
