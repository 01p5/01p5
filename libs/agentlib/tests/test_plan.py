"""
Tests for multi-step Plans.

Validates that Orchestrator.run_plan executes steps in order, threads
prior results forward, short-circuits on failure (unless allow_failure),
and writes one task+result pair per step to the bus log.
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
    ManualRouter,
    Orchestrator,
    Plan,
    PlanStep,
    TaskMessage,
)


class _ScriptedAgent(AgentSpec):
    """Returns a scripted result and records the prompt + inputs it saw."""
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()

    def __init__(self, name: str, status: str = "success", artifacts: dict | None = None):
        self.name = name
        self.domain = name
        self._status = status
        self._artifacts = artifacts or {"by": name}
        self.received: list[TaskMessage] = []

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.received.append(task)
        return AgentResult(
            task_id=task.task_id,
            status=self._status,
            summary=f"{self.name} did its thing",
            artifacts=self._artifacts,
            cost=CostBreakdown(wall_seconds=0.1, total_usd=0.01),
        )


def _orch(*agents: _ScriptedAgent) -> tuple[Orchestrator, InMemoryBus]:
    bus = InMemoryBus()
    ctx = AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())
    orch = Orchestrator(
        bus=bus,
        agents=list(agents),
        ctx=ctx,
        router=ManualRouter(default=agents[0].name),  # unused by run_plan
    )
    return orch, bus


def test_plan_runs_steps_in_order():
    a = _ScriptedAgent("programmer")
    b = _ScriptedAgent("terraform")
    c = _ScriptedAgent("sysadmin")
    orch, bus = _orch(a, b, c)

    plan = Plan(
        plan_id="P1",
        natural_language="deploy a new service",
        steps=[
            PlanStep(agent="programmer", natural_language="generate Dockerfile"),
            PlanStep(agent="terraform", natural_language="apply infra"),
            PlanStep(agent="sysadmin", natural_language="verify pod healthy"),
        ],
    )
    result = orch.run_plan(plan)

    assert result.status == "success"
    assert len(result.step_results) == 3
    # Order: each agent received exactly one task in plan order.
    assert [a.received[0].task_id, b.received[0].task_id, c.received[0].task_id] == [
        "P1:0",
        "P1:1",
        "P1:2",
    ]
    # Every step's task_id is parented to the plan_id.
    for step in (a.received[0], b.received[0], c.received[0]):
        assert step.parent_task_id == "P1"
    # Bus log: 2 messages per step (task + result).
    assert sum(1 for m in bus.log if m.kind == "task") == 3
    assert sum(1 for m in bus.log if m.kind == "result") == 3


def test_prior_results_threaded_into_subsequent_steps():
    a = _ScriptedAgent("programmer", artifacts={"file": "Dockerfile"})
    b = _ScriptedAgent("terraform")
    orch, _ = _orch(a, b)

    plan = Plan(
        plan_id="P2",
        natural_language="x",
        steps=[
            PlanStep(agent="programmer", natural_language="write Dockerfile"),
            PlanStep(agent="terraform", natural_language="apply"),
        ],
    )
    orch.run_plan(plan)

    # Step 1's NL prompt contains a rollup of step 0's result.
    second = b.received[0]
    assert "Prior steps" in second.natural_language
    assert "programmer did its thing" in second.natural_language
    # And the structured prior_results carry artifacts forward.
    prior = second.inputs["prior_results"]
    assert len(prior) == 1
    assert prior[0]["artifacts"] == {"file": "Dockerfile"}
    assert second.inputs["plan_step"] == 1
    assert second.inputs["plan_total_steps"] == 2


def test_failed_step_short_circuits_plan():
    a = _ScriptedAgent("programmer")
    b = _ScriptedAgent("terraform", status="failed")
    c = _ScriptedAgent("sysadmin")
    orch, _ = _orch(a, b, c)

    plan = Plan(
        plan_id="P3",
        natural_language="x",
        steps=[
            PlanStep(agent="programmer", natural_language="s1"),
            PlanStep(agent="terraform", natural_language="s2"),
            PlanStep(agent="sysadmin", natural_language="s3"),
        ],
    )
    result = orch.run_plan(plan)

    assert result.status == "failed"
    assert len(result.step_results) == 2  # third step never ran
    assert c.received == []


def test_allow_failure_keeps_plan_going():
    a = _ScriptedAgent("programmer")
    b = _ScriptedAgent("terraform", status="failed")
    c = _ScriptedAgent("sysadmin")
    orch, _ = _orch(a, b, c)

    plan = Plan(
        plan_id="P4",
        natural_language="x",
        steps=[
            PlanStep(agent="programmer", natural_language="s1"),
            PlanStep(agent="terraform", natural_language="s2", allow_failure=True),
            PlanStep(agent="sysadmin", natural_language="s3"),
        ],
    )
    result = orch.run_plan(plan)

    # Last step ran successfully → plan status reflects the last step,
    # which here is success (no later non-allowed failure).
    assert result.status == "success"
    assert len(result.step_results) == 3
    assert len(c.received) == 1


def test_plan_pinned_to_unknown_agent_raises():
    a = _ScriptedAgent("programmer")
    orch, _ = _orch(a)
    plan = Plan(
        plan_id="P5",
        natural_language="x",
        steps=[PlanStep(agent="ghost", natural_language="s1")],
    )
    try:
        orch.run_plan(plan)
    except ValueError as exc:
        assert "ghost" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown agent")


def test_plan_cost_aggregates_across_steps():
    a = _ScriptedAgent("programmer")
    b = _ScriptedAgent("terraform")
    orch, _ = _orch(a, b)
    plan = Plan(
        plan_id="P6",
        natural_language="x",
        steps=[
            PlanStep(agent="programmer", natural_language="s1"),
            PlanStep(agent="terraform", natural_language="s2"),
        ],
    )
    result = orch.run_plan(plan)
    # Each scripted agent reports 0.1s and $0.01.
    assert result.cost.wall_seconds == 0.2
    assert abs(result.cost.total_usd - 0.02) < 1e-9
