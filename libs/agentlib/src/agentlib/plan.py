"""
Multi-step plans — ordered chains of agent dispatches.

A ``Plan`` is the simplest extension of single-agent dispatch: a list of
steps, each pinned to an agent name, executed sequentially. Each step
sees:

  - The original natural-language request.
  - A summary of every prior step's result (status + summary +
    artifacts) injected into ``inputs["prior_results"]``.

This is *not* a DAG and not a planner. The shape is "human (or a
higher-level planner) writes the steps; the orchestrator runs them in
order with results threaded forward". DAG support and an LLM planner
are W7+ work.

Why pinned agents (no per-step routing): the W4 router decides "which
agent for this NL request" — useful for one-shot tasks. Multi-step
flows are written by someone (human or planner) who already knows the
decomposition; routing each step would introduce nondeterminism the
audit trail does not need.

Failure semantics: a step with ``status != "success"`` short-circuits
the plan unless that step is marked ``allow_failure=True``. The plan's
final result aggregates per-step results into ``artifacts["steps"]``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .spec import AgentResult, CostBreakdown, TaskMessage


@dataclass
class PlanStep:
    """One step in a Plan: which agent runs, what NL request to give it.

    ``inputs`` and ``constraints`` are passed through to the
    ``TaskMessage`` for that step. ``allow_failure`` keeps the plan
    going past a non-success result (used for "best-effort verification"
    steps).
    """
    agent: str
    natural_language: str
    inputs: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    allow_failure: bool = False


@dataclass
class Plan:
    """An ordered list of steps with a stable plan_id linking them all."""
    plan_id: str
    natural_language: str  # the original user request the plan satisfies
    steps: list[PlanStep]


@dataclass
class PlanResult:
    """Aggregate result of running a Plan.

    ``status``:
      - ``"success"`` — every step succeeded, or every failure was
        ``allow_failure=True``.
      - ``"failed"`` — a non-allowed step failed; the plan
        short-circuited.
      - ``"rejected"`` / ``"cancelled"`` — first step that ended in that
        state propagates up.
    """
    plan_id: str
    status: str
    summary: str
    step_results: list[AgentResult]
    cost: CostBreakdown = field(default_factory=CostBreakdown)


def render_prior_results(prior: list[AgentResult]) -> str:
    """Compact human-readable rollup of earlier steps for the next agent.

    The next agent gets this as a header on its NL prompt so it can see
    what's already been done without us inventing a separate context-bus
    convention. Keep it terse — agents have small attention budgets.
    """
    if not prior:
        return ""
    lines = ["Prior steps in this plan:"]
    for i, r in enumerate(prior, 1):
        lines.append(f"  {i}. [{r.status}] {r.summary}")
    return "\n".join(lines) + "\n\n"


def step_to_task(
    plan: Plan,
    step: PlanStep,
    step_index: int,
    prior_results: list[AgentResult],
) -> TaskMessage:
    """Materialize a Plan step into a TaskMessage the orchestrator can dispatch.

    ``parent_task_id`` is set to ``plan.plan_id`` so the audit trail
    can stitch a multi-step run back together.
    """
    rollup = render_prior_results(prior_results)
    return TaskMessage(
        task_id=f"{plan.plan_id}:{step_index}",
        parent_task_id=plan.plan_id,
        natural_language=rollup + step.natural_language,
        inputs={
            **step.inputs,
            "plan_step": step_index,
            "plan_total_steps": len(plan.steps),
            "prior_results": [
                {
                    "task_id": r.task_id,
                    "status": r.status,
                    "summary": r.summary,
                    "artifacts": r.artifacts,
                }
                for r in prior_results
            ],
        },
        constraints=step.constraints,
    )
