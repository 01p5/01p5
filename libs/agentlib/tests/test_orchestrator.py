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
    ManualRouter,
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
