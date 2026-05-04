"""
Cross-agent orchestrator wiring tests.

Validates the W3-4 deliverable "Orchestrator can route tasks to the
correct agent" without depending on the LLM stack:

  - manual_router() routes the right keywords to the right agents.
  - build_orchestrator() returns an Orchestrator that actually
    dispatches to the registered agent on a real bus round-trip.

Real ``AgentSpec`` subclasses need ``StructuralAgent`` (langchain >=1.0).
We sidestep that with a stub that exercises the *runtime contract*
without an LLM.
"""
from __future__ import annotations

from typing import Any, Sequence

from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    AlwaysApprove,
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    TaskMessage,
)
from olympus_cli.registry import (
    KEYWORD_HINTS,
    build_orchestrator,
    manual_router,
)


class _StubAgent(AgentSpec):
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()

    def __init__(self, name: str, domain: str = "stub"):
        self.name = name
        self.domain = domain
        self.handled: list[TaskMessage] = []

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.handled.append(task)
        return AgentResult(
            task_id=task.task_id,
            status="success",
            summary=f"{self.name} handled",
            artifacts={"handled_by": self.name},
            cost=CostBreakdown(),
        )


def _ctx() -> AgentContext:
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_keyword_hints_cover_every_default_agent():
    """Manual router must be able to reach every agent so we don't ship
    an offline mode that strands a domain."""
    target_agents = set(KEYWORD_HINTS.values())
    assert {"sysadmin", "programmer", "terraform", "ansible"} <= target_agents


def test_build_orchestrator_dispatches_via_manual_router():
    agents = [
        _StubAgent("sysadmin", "k8s ops"),
        _StubAgent("programmer", "code packaging"),
        _StubAgent("terraform", "iac"),
        _StubAgent("ansible", "config mgmt"),
    ]
    bus = InMemoryBus()
    orch = build_orchestrator(
        ctx=_ctx(), agents=agents, router=manual_router(), bus=bus
    )

    by_name = {a.name: a for a in agents}

    cases = [
        ("write me a Dockerfile for python", "programmer"),
        ("plan a terraform change to add an S3 bucket", "terraform"),
        ("run the deploy playbook on staging inventory", "ansible"),
        ("why is my pod CrashLoopBackOff", "sysadmin"),
    ]
    for nl, expected in cases:
        task = TaskMessage(task_id=f"T-{expected}", natural_language=nl)
        result = orch.run(task)
        assert result.artifacts["handled_by"] == expected, (nl, expected, result)
        assert by_name[expected].handled, expected

    # Bus log preserves every message for the audit trail.
    log = bus.log
    assert len(log) == 2 * len(cases)  # task + result per case
    assert {m.kind for m in log} == {"task", "result"}


def test_unknown_keyword_falls_back_to_default_agent():
    agents = [_StubAgent("sysadmin"), _StubAgent("terraform")]
    orch = build_orchestrator(
        ctx=_ctx(), agents=agents, router=manual_router(default="sysadmin")
    )
    task = TaskMessage(task_id="T-fallback", natural_language="something nobody mapped")
    result = orch.run(task)
    assert result.artifacts["handled_by"] == "sysadmin"
