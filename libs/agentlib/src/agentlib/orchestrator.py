"""
Orchestrator — receives a task, picks an agent (router), dispatches via bus.

v1 design choices (per AGENT_SPEC.md "Open decisions"):
  - Orchestrator-only delegation: agents do not publish ``kind="task"`` to
    each other. The orchestrator is the only sender of tasks.
  - Synchronous: ``run(task)`` blocks until the agent's result message
    arrives on the bus. Async/streaming progress is v2.

The router is pluggable: ``LLMRouter`` uses a small StructuralAgent to
choose; ``ManualRouter`` returns a fixed mapping for tests.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .bus import BusMessage, InMemoryBus, new_message
from .spec import AgentContext, AgentResult, AgentSpec, CostBreakdown, TaskMessage


class Router(Protocol):
    def route(self, task: TaskMessage) -> str:
        """Return the name of the agent that should handle this task."""


class ManualRouter:
    """Static mapping. Used by tests and any deterministic deployment path."""

    def __init__(self, default: str, by_keyword: Optional[dict[str, str]] = None):
        self.default = default
        self.by_keyword = by_keyword or {}

    def route(self, task: TaskMessage) -> str:
        text = task.natural_language.lower()
        for kw, agent in self.by_keyword.items():
            if kw.lower() in text:
                return agent
        return self.default


class _RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str = Field(description="Name of the agent that should handle this task.")
    rationale: str = Field(description="One-sentence reason.")


class LLMRouter:
    """LLM-driven routing. Imported lazily to keep the orchestrator
    importable without an LLM key for tests."""

    def __init__(self, agent_descriptions: dict[str, str], model: Optional[str] = None):
        self.agent_descriptions = agent_descriptions
        self.model = model

    def route(self, task: TaskMessage) -> str:
        from .main import StructuralAgent
        from .models import claude45

        catalog = "\n".join(
            f"  - {name}: {desc}" for name, desc in self.agent_descriptions.items()
        )
        prompt = (
            f"Olympus task router. Available agents:\n{catalog}\n\n"
            f"Task: {task.natural_language}\n\n"
            "Pick exactly one agent by name."
        )
        agent = StructuralAgent(
            task_id=f"router:{task.task_id}",
            system_prompt="You route DevOps tasks to the right specialist agent. Be decisive.",
            response_class=_RouteDecision,
            model=self.model or claude45,
            agent_type="orchestrator-router",
        )
        try:
            decision = agent.invoke(prompt)
        finally:
            agent.cleanup()
        if decision.agent_name not in self.agent_descriptions:
            raise ValueError(
                f"router returned unknown agent {decision.agent_name!r}; "
                f"valid: {list(self.agent_descriptions)}"
            )
        return decision.agent_name


class Orchestrator:
    """Owns the bus subscriptions for every registered agent.

    Lifecycle:
      - Construct with a bus, agent list, and shared AgentContext.
      - Each agent gets a subscription on the bus for its own name.
      - ``run(task)`` publishes a task message and blocks on the result.
    """

    def __init__(
        self,
        bus: InMemoryBus,
        agents: Sequence[AgentSpec],
        ctx: AgentContext,
        router: Optional[Router] = None,
    ):
        self.bus = bus
        self.agents = {a.name: a for a in agents}
        self.ctx = ctx
        self.router = router or LLMRouter(
            {a.name: a.domain for a in agents}
        )
        self._results: dict[str, AgentResult] = {}

        for name, agent in self.agents.items():
            # Bind agent into the closure so each subscription dispatches to
            # the right handler.
            self.bus.subscribe(name, self._make_agent_handler(agent))
        self.bus.subscribe("orchestrator", self._on_orchestrator_msg)

    def _make_agent_handler(self, agent: AgentSpec):
        def handler(msg: BusMessage) -> None:
            if msg.kind != "task":
                return
            payload = msg.payload
            task = payload if isinstance(payload, TaskMessage) else TaskMessage(**payload)
            result = agent.handle(task, self.ctx)
            self.bus.publish(
                new_message(
                    task_id=task.task_id,
                    sender=agent.name,
                    recipient="orchestrator",
                    kind="result",
                    payload=result,
                    causation_id=msg.msg_id,
                )
            )
        return handler

    def _on_orchestrator_msg(self, msg: BusMessage) -> None:
        if msg.kind != "result":
            return
        result = msg.payload
        if not isinstance(result, AgentResult):
            result = _result_from_dict(result)
        self._results[msg.task_id] = result

    def run(self, task: TaskMessage) -> AgentResult:
        agent_name = self.router.route(task)
        if agent_name not in self.agents:
            raise ValueError(
                f"router returned unknown agent {agent_name!r}; "
                f"registered: {list(self.agents)}"
            )
        self.bus.publish(
            new_message(
                task_id=task.task_id,
                sender="orchestrator",
                recipient=agent_name,
                kind="task",
                payload=task,
            )
        )
        # Synchronous bus: the result is already in _results by the time
        # publish() returns. If we move to async, this becomes a wait().
        if task.task_id not in self._results:
            raise RuntimeError(
                f"agent {agent_name!r} did not produce a result on the bus"
            )
        return self._results.pop(task.task_id)


def _result_from_dict(d: dict) -> AgentResult:
    cost_data = d.get("cost", {})
    cost = CostBreakdown(**cost_data) if isinstance(cost_data, dict) else cost_data
    return AgentResult(**{**d, "cost": cost})
