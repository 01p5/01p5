"""Ticket cancellation — the chat "Stop" button.

Cancelling a ticket must stop EVERY agent under it: new dispatches return
``cancelled`` (no agent runs), the coordinator's dispatcher/resolver closures
raise ``CancelledError`` to unwind its invoke, and an in-flight specialist's
gated tool call raises ``CancelledError`` at its next call.
"""
from __future__ import annotations

import threading
from typing import Any, Sequence

import pytest
from langchain_core.tools import StructuredTool

from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    AlwaysApprove,
    CancelledError,
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    ManualRouter,
    Orchestrator,
    TaskMessage,
    gate_tools,
)


class _Echo(AgentSpec):
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()

    def __init__(self, name: str):
        self.name = name
        self.domain = "t"
        self.calls = 0

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.calls += 1
        return AgentResult(task_id=task.task_id, status="success", summary="ok",
                           cost=CostBreakdown())


def _orch():
    a = _Echo("alpha")
    return Orchestrator(bus=InMemoryBus(), agents=[a],
                        ctx=AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger()),
                        router=ManualRouter(default="alpha")), a


def _task(tid: str) -> TaskMessage:
    return TaskMessage(task_id="x", natural_language="go", ticket_id=tid, parent_task_id=tid)


def test_cancelled_ticket_skips_the_agent():
    orch, a = _orch()
    orch.cancel_ticket("TK")
    r = orch.dispatch_to("alpha", _task("TK"), announce=False)
    assert r.status == "cancelled"
    assert a.calls == 0  # the agent never ran — auto-dispatch disabled


def test_clear_cancel_restores_normal_dispatch():
    orch, a = _orch()
    orch.cancel_ticket("TK")
    orch.clear_cancel("TK")
    r = orch.dispatch_to("alpha", _task("TK"), announce=False)
    assert r.status == "success"
    assert a.calls == 1


def test_dispatcher_and_resolver_closures_raise_when_cancelled():
    orch, _ = _orch()
    orch.cancel_ticket("TK")
    dispatcher = orch._make_ticket_dispatcher("TK")
    resolver = orch._make_ticket_resolver("TK")
    with pytest.raises(CancelledError):
        dispatcher("alpha", "do it")
    with pytest.raises(CancelledError):
        resolver("alpha", "?")


def test_gate_tools_aborts_in_flight_tool_on_cancel():
    ran = {"n": 0}

    def ping() -> str:
        ran["n"] += 1
        return "pong"

    class _Spec(AgentSpec):
        name = "alpha"
        domain = "t"
        tools = [StructuredTool.from_function(func=ping, name="ping", description="p")]
        destructive_verbs: set[str] = set()

        def handle(self, task, ctx):  # pragma: no cover - not used here
            return AgentResult(task_id=task.task_id, status="success", summary="", cost=CostBreakdown())

    ev = threading.Event()
    ev.set()  # ticket already cancelled
    ctx = AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger(), cancel_token=ev)
    gated = gate_tools(_Spec(), ctx, task_id="t1", ticket_id="TK")
    with pytest.raises(CancelledError):
        gated[0].invoke({})
    assert ran["n"] == 0  # the underlying tool never executed
