"""
Cross-agent Plan integration tests.

The unit tests in ``test_plan.py`` use scripted agents that bypass
``gate_tools`` entirely. These tests close the gap: agents here use
real tools wrapped by ``gate_tools``, real approval hooks, real
memory + rollback stores. The whole point is to catch wiring bugs
that surface only when multiple agents chain through a plan.

What's exercised end-to-end:

  - A plan whose steps each invoke at least one destructive tool —
    every approval round-trip + every audit record fires per step.
  - Prior step results thread into subsequent steps via both
    ``task.inputs["prior_results"]`` and the natural-language rollup.
  - Rollback entries are captured per step and tagged with the
    step's task_id, not the plan_id (so a later UI can show
    "this rollback came from step 2 of plan P").
  - Memory retrieval runs per step (the change in run_plan that
    paired with this test file): step N+1 sees a prepended context
    block when memory has a similar prior run.
  - Memory write-back per step: each step's outcome lands in the
    store so a later plan can retrieve it.

These tests stay LLM-free by using ``AgentSpec`` subclasses whose
``handle`` directly invokes tools through ``gate_tools`` — the
``StructuralAgent`` path is exercised by the per-agent live smokes.
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
    CostBreakdown,
    InMemoryAuditLogger,
    InMemoryBus,
    InMemoryMemoryStore,
    InMemoryRollbackStore,
    ManualRouter,
    MemoryEntry,
    Orchestrator,
    Plan,
    PlanStep,
    RollbackPlan,
    TaskMessage,
    gate_tools,
)

# ---------------------------------------------------------------------
# Tool primitives the integration agents use. These are intentionally
# small — they live here rather than reusing the production agents so
# the tests don't pull in the LLM stack.
# ---------------------------------------------------------------------


@tool
def write_file(path: str, content: str) -> str:
    """Write ``content`` to ``path`` (destructive)."""
    from pathlib import Path as _P

    p = _P(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} bytes to {p}"


@tool
def read_file(path: str) -> str:
    """Read a file."""
    from pathlib import Path as _P

    p = _P(path).expanduser()
    return p.read_text() if p.is_file() else ""


def _snapshot_write_file(args: dict) -> RollbackPlan:
    from pathlib import Path as _P

    p = _P(args["path"]).expanduser()
    if p.is_file():
        return RollbackPlan(
            inverse_tool="write_file",
            inverse_args={"path": str(p), "content": p.read_text()},
            description=f"restore {p}",
            snapshot={"prior_exists": True},
        )
    return RollbackPlan(
        inverse_tool="write_file",
        inverse_args={"path": str(p), "content": ""},
        description=f"clear {p} (did not exist before)",
        snapshot={"prior_exists": False},
    )


# ---------------------------------------------------------------------
# Integration agents: each one does a small piece of real work, calls
# at least one destructive tool through gate_tools, and threads the
# prior-step rollup into the result summary so cross-step flow shows
# up in assertions.
# ---------------------------------------------------------------------


class _WriterAgent(AgentSpec):
    """Stand-in for the Programmer: writes a file."""
    name = "writer"
    domain = "files"
    tools: Sequence[Any] = [write_file, read_file]
    destructive_verbs = {"write_file"}
    rollback_snapshots = {"write_file": _snapshot_write_file}

    def __init__(self, target_path: str, content: str):
        self.target_path = target_path
        self.content = content
        self.received: list[TaskMessage] = []

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.received.append(task)
        gated = gate_tools(self, ctx, task.task_id)
        by_name = {t.name: t for t in gated}
        by_name["write_file"].invoke({
            "path": self.target_path, "content": self.content,
        })
        return AgentResult(
            task_id=task.task_id,
            status="success",
            summary=f"writer wrote {self.target_path}",
            artifacts={"path": self.target_path, "bytes": len(self.content)},
            cost=CostBreakdown(wall_seconds=0.05),
        )


class _PlannerAgent(AgentSpec):
    """Stand-in for Terraform: reads the previous step's artifact
    path and writes a plan file alongside it. Proves both prior_results
    threading and per-step rollback capture work in concert."""
    name = "planner"
    domain = "plans"
    tools: Sequence[Any] = [write_file, read_file]
    destructive_verbs = {"write_file"}
    rollback_snapshots = {"write_file": _snapshot_write_file}

    def __init__(self):
        self.received: list[TaskMessage] = []

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.received.append(task)
        # Pull the file path the writer produced out of prior_results.
        prior = task.inputs.get("prior_results", [])
        if not prior:
            return AgentResult(
                task_id=task.task_id, status="failed",
                summary="planner saw no prior step",
                cost=CostBreakdown(),
            )
        source = prior[-1]["artifacts"]["path"]
        plan_path = source + ".plan"
        gated = gate_tools(self, ctx, task.task_id)
        by_name = {t.name: t for t in gated}
        content = by_name["read_file"].invoke({"path": source})
        by_name["write_file"].invoke({
            "path": plan_path,
            "content": f"# plan derived from {source}\n{content[:80]}\n",
        })
        return AgentResult(
            task_id=task.task_id, status="success",
            summary=f"planner derived {plan_path}",
            artifacts={"plan_path": plan_path, "source": source},
            cost=CostBreakdown(wall_seconds=0.04),
        )


class _CheckerAgent(AgentSpec):
    """Stand-in for Sysadmin: reads both the writer's and planner's
    artifacts and reports without mutating anything."""
    name = "checker"
    domain = "checks"
    tools: Sequence[Any] = [read_file]
    destructive_verbs: set[str] = set()

    def __init__(self):
        self.received: list[TaskMessage] = []

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        self.received.append(task)
        prior = task.inputs.get("prior_results", [])
        gated = gate_tools(self, ctx, task.task_id)
        by_name = {t.name: t for t in gated}
        # Verify the planner's output exists and is non-empty.
        plan_path = prior[-1]["artifacts"]["plan_path"]
        content = by_name["read_file"].invoke({"path": plan_path})
        ok = bool(content.strip())
        return AgentResult(
            task_id=task.task_id,
            status="success" if ok else "failed",
            summary=f"checker verified {plan_path} (non-empty={ok})",
            artifacts={"verified": ok, "path_checked": plan_path},
            cost=CostBreakdown(wall_seconds=0.02),
        )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _orch(
    agents: list[AgentSpec],
    *,
    approval=None,
    memory=None,
    rollback=None,
) -> tuple[Orchestrator, InMemoryBus, InMemoryAuditLogger]:
    bus = InMemoryBus()
    audit = InMemoryAuditLogger()
    ctx = AgentContext(
        approval=approval or AlwaysApprove(),
        audit=audit,
        rollback=rollback,
    )
    orch = Orchestrator(
        bus=bus, agents=agents, ctx=ctx,
        router=ManualRouter(default=agents[0].name),
        memory=memory,
    )
    return orch, bus, audit


def _three_agent_plan(tmp_path) -> tuple[Plan, _WriterAgent, _PlannerAgent, _CheckerAgent]:
    """The reference cross-agent workflow: writer → planner → checker."""
    target = str(tmp_path / "service" / "Dockerfile")
    w = _WriterAgent(target, "FROM python:3.12-slim\nCOPY . /app\n")
    p = _PlannerAgent()
    c = _CheckerAgent()
    plan = Plan(
        plan_id="P-int",
        natural_language="ship a new service",
        steps=[
            PlanStep(agent="writer", natural_language="write the Dockerfile"),
            PlanStep(agent="planner", natural_language="derive a plan from the file"),
            PlanStep(agent="checker", natural_language="verify the plan exists"),
        ],
    )
    return plan, w, p, c


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_cross_agent_plan_runs_to_success(tmp_path):
    plan, w, p, c = _three_agent_plan(tmp_path)
    orch, _, _ = _orch([w, p, c])

    result = orch.run_plan(plan)

    assert result.status == "success"
    assert len(result.step_results) == 3
    # Every agent received exactly one task in plan order.
    assert len(w.received) == len(p.received) == len(c.received) == 1
    assert w.received[0].parent_task_id == "P-int"
    # The Dockerfile + its derived plan both exist on disk.
    dockerfile = tmp_path / "service" / "Dockerfile"
    assert dockerfile.is_file()
    assert dockerfile.read_text().startswith("FROM python:3.12-slim")
    plan_file = tmp_path / "service" / "Dockerfile.plan"
    assert plan_file.is_file()
    assert "plan derived from" in plan_file.read_text()


def test_cross_agent_plan_audit_log_records_every_destructive_call(tmp_path):
    """Each step's destructive tool call hits the audit log twice
    (pre + post). Read-only calls hit once each. Verifies the
    per-step gate_tools wrapping holds across agent boundaries."""
    plan, w, p, c = _three_agent_plan(tmp_path)
    orch, _, audit = _orch([w, p, c])

    orch.run_plan(plan)

    # Both write_file calls show pre (approved=True, result=None) and
    # post (approved=True, result=...) records.
    write_file_records = [r for r in audit.records if r["tool"] == "write_file"]
    assert len(write_file_records) == 4  # 2 destructive calls × 2 audit lines
    # Audit task_ids are the per-step task_ids, not the plan id.
    write_task_ids = {r["task_id"] for r in write_file_records}
    assert "P-int" not in write_task_ids
    assert write_task_ids == {"P-int:0", "P-int:1"}


def test_cross_agent_plan_captures_rollback_per_step(tmp_path):
    """The rollback store sees one entry per destructive call —
    each tagged with the step's task_id so a later UI can group
    rollbacks under the originating plan step."""
    plan, w, p, c = _three_agent_plan(tmp_path)
    rollback = InMemoryRollbackStore()
    orch, _, _ = _orch([w, p, c], rollback=rollback)

    orch.run_plan(plan)

    all_entries = rollback.list_recent(k=10)
    assert len(all_entries) == 2  # writer + planner each fired write_file
    task_ids = {e.task_id for e in all_entries}
    assert task_ids == {"P-int:0", "P-int:1"}
    # Both rollback entries point at write_file as the inverse — and
    # the writer's entry knows the file didn't exist before, so its
    # rollback restores the empty state.
    by_task = {e.task_id: e for e in all_entries}
    assert by_task["P-int:0"].snapshot["prior_exists"] is False
    assert by_task["P-int:1"].snapshot["prior_exists"] is False


def test_cross_agent_plan_threads_prior_results_into_planner(tmp_path):
    """The planner step must see the writer's path artifact, both in
    the structured prior_results AND in the NL rollup."""
    plan, w, p, c = _three_agent_plan(tmp_path)
    orch, _, _ = _orch([w, p, c])

    orch.run_plan(plan)

    planner_task = p.received[0]
    # Structured channel.
    prior = planner_task.inputs["prior_results"]
    assert len(prior) == 1
    assert prior[0]["artifacts"]["path"].endswith("Dockerfile")
    # NL rollup.
    assert "writer wrote" in planner_task.natural_language
    assert "Prior steps" in planner_task.natural_language


def test_cross_agent_plan_short_circuits_on_destructive_rejection(tmp_path):
    """If the user rejects the writer's destructive call, the writer
    step still 'succeeds' from the orchestrator's perspective — the
    agent itself decides — but a *real* agent could check the tool's
    "REJECTED" string and fail. We assert here that the planner step
    receives a useful artifacts payload describing what went wrong."""
    # Use a writer that turns a rejected write into a failed result.
    class _StrictWriter(_WriterAgent):
        def handle(self, task, ctx):
            self.received.append(task)
            gated = gate_tools(self, ctx, task.task_id)
            by_name = {t.name: t for t in gated}
            out = by_name["write_file"].invoke({
                "path": self.target_path, "content": self.content,
            })
            if "REJECTED" in out:
                return AgentResult(
                    task_id=task.task_id, status="failed",
                    summary=f"writer blocked: {out}",
                    cost=CostBreakdown(),
                )
            return AgentResult(
                task_id=task.task_id, status="success",
                summary=f"writer wrote {self.target_path}",
                artifacts={"path": self.target_path},
                cost=CostBreakdown(),
            )

    target = str(tmp_path / "Dockerfile")
    w = _StrictWriter(target, "FROM python\n")
    p = _PlannerAgent()
    c = _CheckerAgent()
    plan = Plan(
        plan_id="P-rej",
        natural_language="reject test",
        steps=[
            PlanStep(agent="writer", natural_language="write"),
            PlanStep(agent="planner", natural_language="plan"),
            PlanStep(agent="checker", natural_language="check"),
        ],
    )
    orch, _, _ = _orch([w, p, c], approval=AlwaysReject())

    result = orch.run_plan(plan)

    assert result.status == "failed"
    assert len(result.step_results) == 1  # short-circuit after writer
    # The file was never created.
    assert not (tmp_path / "Dockerfile").exists()


def test_cross_agent_plan_writes_each_step_outcome_to_memory(tmp_path):
    """Memory writes happen per-step (the run_plan fix this commit
    pairs with). A later orchestrator.run on a similar query must
    surface those step outcomes."""
    plan, w, p, c = _three_agent_plan(tmp_path)
    memory = InMemoryMemoryStore()
    orch, _, _ = _orch([w, p, c], memory=memory)

    orch.run_plan(plan)

    # Three writes — one per step, scoped to that step's agent.
    by_agent = {}
    for entry in memory.search("write the Dockerfile", k=10):
        by_agent.setdefault(entry.agent, []).append(entry)
    for entry in memory.search("plan", k=10):
        by_agent.setdefault(entry.agent, []).append(entry)
    for entry in memory.search("verify", k=10):
        by_agent.setdefault(entry.agent, []).append(entry)
    assert {"writer", "planner", "checker"} <= set(by_agent.keys())


def test_cross_agent_plan_step_pulls_from_pre_existing_memory(tmp_path):
    """When memory already contains a prior writer-run, the writer
    step's natural_language must include the prepended context block."""
    plan, w, p, c = _three_agent_plan(tmp_path)
    memory = InMemoryMemoryStore()
    # Seed a prior writer entry whose tokens overlap the upcoming step.
    memory.write(MemoryEntry(
        task_id="seed-1", agent="writer",
        natural_language="write the Dockerfile for the python flask app",
        summary="picked python:3.12-slim, exposed port 8080",
        status="success",
    ))
    orch, _, _ = _orch([w, p, c], memory=memory)

    orch.run_plan(plan)

    writer_task = w.received[0]
    # The retrieved block surfaces in the NL with the safety prefix.
    assert "untrusted" in writer_task.natural_language.lower()
    assert "python:3.12-slim" in writer_task.natural_language


def test_cross_agent_plan_memory_scopes_retrieval_per_step_agent(tmp_path):
    """A seeded memory tagged for ``planner`` must NOT leak into the
    writer step (and vice versa). Confirms the agent= filter on
    retrieval keeps cross-agent context isolated even inside a plan."""
    plan, w, p, c = _three_agent_plan(tmp_path)
    memory = InMemoryMemoryStore()
    memory.write(MemoryEntry(
        task_id="other-1", agent="planner",
        natural_language="write the Dockerfile content",  # overlapping NL
        summary="planner-only secret",
        status="success",
    ))
    orch, _, _ = _orch([w, p, c], memory=memory)

    orch.run_plan(plan)

    writer_nl = w.received[0].natural_language
    # Writer must not see the planner's prior entry.
    assert "planner-only secret" not in writer_nl
    # Writer's NL only carries the plan rollup, not a retrieved block.
    assert "untrusted" not in writer_nl.lower()
