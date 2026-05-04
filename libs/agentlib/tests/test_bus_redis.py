"""
Tests for RedisStreamsBus.

Uses ``fakeredis`` (in-process Redis emulator) to avoid spinning up a
real Redis container in CI. The whole point of ``RedisStreamsBus`` is
that it implements the same shape as ``InMemoryBus`` — these tests
verify exactly that, plus the orchestrator integration.

Skipped automatically if fakeredis is not installed.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Sequence

import pytest

fakeredis = pytest.importorskip("fakeredis")

from agentlib import (  # noqa: E402
    AgentContext,
    AgentResult,
    AgentSpec,
    AlwaysApprove,
    BusMessage,
    CostBreakdown,
    InMemoryAuditLogger,
    ManualRouter,
    Orchestrator,
    RedisStreamsBus,
    TaskMessage,
    new_message,
)


def _bus() -> RedisStreamsBus:
    client = fakeredis.FakeStrictRedis()
    return RedisStreamsBus(client)


def _wait(predicate, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_publish_then_subscribe_delivers_only_new_messages():
    """Subscribe-after-publish should NOT replay history; that matches
    InMemoryBus's "$ from now" semantics."""
    bus = _bus()
    bus.publish(
        new_message(task_id="T1", sender="orchestrator", recipient="alpha", kind="task", payload={"x": 1})
    )

    seen: list[BusMessage] = []
    bus.subscribe("alpha", seen.append)
    # Give the consumer thread a beat to start reading.
    time.sleep(0.1)
    assert seen == []

    bus.publish(
        new_message(task_id="T2", sender="orchestrator", recipient="alpha", kind="task", payload={"x": 2})
    )
    assert _wait(lambda: len(seen) == 1)
    assert seen[0].task_id == "T2"
    assert seen[0].payload == {"x": 2}
    bus.close()


def test_broadcast_subscriber_receives_every_message():
    bus = _bus()
    seen: list[BusMessage] = []
    bus.subscribe("*", seen.append)
    time.sleep(0.05)

    for tid in ("A", "B", "C"):
        bus.publish(
            new_message(task_id=tid, sender="orchestrator", recipient="alpha", kind="task", payload={})
        )

    assert _wait(lambda: len(seen) == 3)
    assert {m.task_id for m in seen} == {"A", "B", "C"}
    bus.close()


def test_log_property_captures_full_history():
    """``bus.log`` mirrors the all-stream so post-hoc audit reads work."""
    bus = _bus()
    time.sleep(0.05)  # allow log-tailer to start at "0"

    bus.publish(new_message(task_id="T1", sender="o", recipient="alpha", kind="task", payload={}))
    bus.publish(new_message(task_id="T1", sender="alpha", recipient="orchestrator", kind="result", payload={}))

    assert _wait(lambda: len(bus.log) == 2)
    kinds = [m.kind for m in bus.log]
    assert kinds == ["task", "result"]
    bus.close()


def test_dataclass_payloads_round_trip_as_dicts():
    """The bus is payload-agnostic — receivers re-hydrate. Verify that a
    TaskMessage payload survives encoding as a dict."""
    bus = _bus()
    seen: list[BusMessage] = []
    bus.subscribe("alpha", seen.append)
    time.sleep(0.05)

    task = TaskMessage(task_id="T1", natural_language="hello", inputs={"k": 1})
    bus.publish(
        new_message(task_id=task.task_id, sender="o", recipient="alpha", kind="task", payload=task)
    )
    assert _wait(lambda: len(seen) == 1)

    # Receiver gets a plain dict (envelope is JSON), and can rebuild the
    # dataclass — same convention the orchestrator already uses.
    payload = seen[0].payload
    assert isinstance(payload, dict)
    rebuilt = TaskMessage(**payload)
    assert rebuilt.natural_language == "hello"
    assert rebuilt.inputs == {"k": 1}
    bus.close()


# ---- orchestrator integration ----


class _EchoAgent(AgentSpec):
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()

    def __init__(self, name: str):
        self.name = name
        self.domain = name
        self.handled: list[TaskMessage] = []

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.handled.append(task)
        return AgentResult(
            task_id=task.task_id,
            status="success",
            summary=f"{self.name} handled",
            artifacts={"by": self.name},
            cost=CostBreakdown(),
        )


def test_orchestrator_runs_against_redis_bus():
    """End-to-end: route → publish task on Redis → consumer thread runs
    agent → result published → orchestrator unblocks."""
    bus = _bus()
    agent = _EchoAgent("alpha")
    orch = Orchestrator(
        bus=bus,
        agents=[agent],
        ctx=AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger()),
        router=ManualRouter(default="alpha"),
        result_timeout_seconds=3.0,
    )

    # Subscriptions take a beat to start consuming on Redis.
    time.sleep(0.1)

    result = orch.run(TaskMessage(task_id="T1", natural_language="x"))
    assert result.status == "success"
    assert result.artifacts["by"] == "alpha"
    assert len(agent.handled) == 1

    # Bus log shows the round-trip.
    assert _wait(lambda: len(bus.log) >= 2)
    kinds = sorted(m.kind for m in bus.log)
    assert kinds == ["result", "task"]
    bus.close()


def test_orchestrator_times_out_when_no_result_arrives():
    """If a task never finds a registered consumer (subscribe never ran),
    ``run()`` raises TimeoutError instead of hanging forever."""
    bus = _bus()
    # Register the agent in the orchestrator but DROP the bus subscription
    # by using a stub that never calls handler — easiest way: an agent
    # name with no consumer thread reading its stream.
    class _OrphanAgent(AgentSpec):
        tools: Sequence[Any] = []
        destructive_verbs: set[str] = set()
        name = "orphan"
        domain = "x"
        def handle(self, task, ctx):  # pragma: no cover — never called
            raise AssertionError

    orch = Orchestrator.__new__(Orchestrator)
    orch.bus = bus
    orch.agents = {"orphan": _OrphanAgent()}
    orch.ctx = AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())
    orch.router = ManualRouter(default="orphan")
    orch._result_timeout = 0.3
    orch._results = {}
    orch._result_events = {}
    orch._results_lock = threading.Lock()
    # Note: deliberately skipping bus.subscribe() so no consumer
    # exists for the "orphan" stream.

    with pytest.raises(TimeoutError):
        orch.run(TaskMessage(task_id="T-orphan", natural_language="x"))
    bus.close()
