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

import json
import logging
import threading
import uuid
from dataclasses import replace
from typing import Any, Callable, Optional, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .bus import Bus, BusMessage, new_message
from .memory import MemoryEntry, MemoryStore, NullMemoryStore, render_memory_block
from .plan import Plan, PlanResult, step_to_task
from .spec import AgentContext, AgentResult, AgentSpec, CancelledError, CostBreakdown, TaskMessage
from .ticket import TicketStore, ticket_bus_sink

logger = logging.getLogger(__name__)


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
        from .models import gpt55

        catalog = "\n".join(
            f"  - {name}: {desc}" for name, desc in self.agent_descriptions.items()
        )
        prompt = (
            f"Olympus task router. Available agents:\n{catalog}\n\n"
            f"Task: {task.natural_language}\n\n"
            "Pick exactly one agent by name. "
            "If the task is to AUTHOR / CREATE / WRITE / EDIT source "
            "files for any tool (terraform .tf, ansible .yml, Dockerfiles, "
            "Helm values, compose blocks, scripts), route to the "
            "programmer agent — it owns generation + write_file. The "
            "terraform / ansible / sysadmin agents only EXECUTE existing "
            "stacks / playbooks / kubectl commands; they cannot author.\n"
            "\n"
            "For HOST-LEVEL introspection on the cluster nodes themselves "
            "— free disk space, uptime, memory usage, file existence, "
            "package versions, anything that needs an SSH command on a "
            "node — route to ANSIBLE. It has the inventory and SSH key "
            "wired in. The sysadmin agent only sees the cluster through "
            "kubectl (pod/node objects, events, logs) — it can't query "
            "actual filesystem state on nodes."
        )
        agent = StructuralAgent(
            task_id=f"router:{task.task_id}",
            system_prompt="You route DevOps tasks to the right specialist agent. Be decisive.",
            response_class=_RouteDecision,
            model=self.model or gpt55,
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
        bus: Bus,
        agents: Sequence[AgentSpec],
        ctx: AgentContext,
        router: Optional[Router] = None,
        result_timeout_seconds: float = 600.0,
        memory: Optional[MemoryStore] = None,
        memory_k: int = 3,
        ticket_store: Optional[TicketStore] = None,
        checkpointer_factory: Optional[Callable[[], Any]] = None,
    ):
        self.bus = bus
        self.agents = {a.name: a for a in agents}
        self.ctx = ctx
        # --- group-chat ticket machinery (Phase 2) ---
        # The transcript is a projection of the bus: when a ticket store is
        # given we subscribe it on "*" exactly like the audit sinks, so
        # dispatch/result traffic lands in the transcript automatically.
        self.ticket_store = ticket_store
        if ticket_store is not None:
            self.bus.subscribe("*", ticket_bus_sink(ticket_store))
        # One long-lived checkpoint saver per (ticket_id, agent_name) so an
        # agent's context survives across dispatch / ask_agent re-invocations
        # within a ticket. Lazily created; discarded on ticket close (P3).
        self._checkpointer_factory = checkpointer_factory or _default_checkpointer
        self._ticket_checkpointers: dict[tuple[str, str], Any] = {}
        # Guards against re-entrant invocation of an agent already on the
        # call stack within the same ticket (e.g. A asks A, or main asks
        # main) — prevents infinite recursion through ask_agent / dispatch.
        self._active_in_ticket: set[tuple[str, str]] = set()
        self._ticket_lock = threading.RLock()
        # Per-ticket cancellation. The chat "Stop" button sets the event; it
        # propagates to EVERY agent in the ticket — the coordinator (via the
        # dispatcher/resolver closures) and any in-flight specialist (via
        # gate_tools, which sees it as ctx.cancel_token) — so the whole turn
        # unwinds and no further auto-dispatch happens.
        self._ticket_cancels: dict[str, threading.Event] = {}
        # Per-turn cost accumulator, keyed by ticket_id. Collects every
        # agent's cost across the whole dispatch tree (main + specialists +
        # ask_agent) so the outermost dispatch_to(aggregate_cost=True) can sum
        # them — per-user accounting then reflects sub-agent spend, not just
        # the coordinator's. NOT thread-local: LangGraph runs tool calls (and
        # thus nested dispatches) on a ThreadPoolExecutor, so the sub-agent
        # runs on a different thread than the one that opened the turn. Every
        # agent in a turn shares the ticket_id (the dispatcher preserves it),
        # so we key on that and guard with the existing ticket lock.
        self._turn_costs: dict[str, list[CostBreakdown]] = {}
        # Start with the catalog filtered against an empty prerequisite
        # set — agents with no prereqs are still in, anything that
        # depends on an MCP server is held out until the dashboard
        # calls refresh_active_agents() with the connected names.
        initial_catalog = {
            a.name: a.domain for a in agents
            if not getattr(a, "prerequisites", None)
            and getattr(a, "routable", True)
        }
        self.router = router or LLMRouter(initial_catalog)
        self._result_timeout = result_timeout_seconds
        self.memory: MemoryStore = memory or NullMemoryStore()
        self._memory_k = memory_k
        self._results: dict[str, AgentResult] = {}
        self._result_events: dict[str, threading.Event] = {}
        self._results_lock = threading.Lock()

        for name, agent in self.agents.items():
            # Bind agent into the closure so each subscription dispatches to
            # the right handler.
            self.bus.subscribe(name, self._make_agent_handler(agent))
        self.bus.subscribe("orchestrator", self._on_orchestrator_msg)

    def refresh_active_agents(self, fulfilled_prereqs: set[str]) -> None:
        """Refilter the router's agent catalog against a set of
        currently-met prerequisites (e.g. the names of connected MCP
        servers). Idempotent — call it whenever the prerequisite set
        changes (typically after an MCP server is added or removed).

        Agents whose ``prerequisites`` are a subset of ``fulfilled_prereqs``
        appear in the router's candidate list; the rest are silently
        held out. Doesn't touch bus subscriptions — handle() still runs
        if a message is delivered directly to the agent's name, this
        is purely about router pickup."""
        active = {
            a.name: a.domain for a in self.agents.values()
            if getattr(a, "prerequisites", set()) <= fulfilled_prereqs
            and getattr(a, "routable", True)
        }
        # Static-dict router: just mutate. Future routers can override
        # this by implementing their own .agent_descriptions surface.
        descriptions = getattr(self.router, "agent_descriptions", None)
        if isinstance(descriptions, dict):
            descriptions.clear()
            descriptions.update(active)

    def _make_agent_handler(self, agent: AgentSpec):
        def handler(msg: BusMessage) -> None:
            if msg.kind != "task":
                return
            payload = msg.payload
            task = payload if isinstance(payload, TaskMessage) else TaskMessage(**payload)
            result = agent.handle(task, self.ctx)
            self._emit_agent_cost(agent.name, result)
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
        with self._results_lock:
            self._results[msg.task_id] = result
            event = self._result_events.get(msg.task_id)
        if event is not None:
            event.set()

    def run(self, task: TaskMessage) -> AgentResult:
        agent_name = self.router.route(task)
        task = self._with_memory_context(task, agent=agent_name)
        result = self._dispatch(task, agent_name)
        self._remember(task, agent_name, result)
        return result

    def _with_memory_context(self, task: TaskMessage, agent: str) -> TaskMessage:
        """Search memory for similar past runs and prepend them to the
        natural-language prompt. Filter by agent so a Sysadmin task
        doesn't pull in Terraform-shaped context (cross-agent
        retrieval is a later refinement)."""
        if isinstance(self.memory, NullMemoryStore):
            return task
        try:
            past = self.memory.search(
                task.natural_language, k=self._memory_k, agent=agent
            )
        except Exception:
            # Memory must never block dispatch.
            return task
        block = render_memory_block(past)
        if not block:
            return task
        return replace(task, natural_language=f"{block}{task.natural_language}")

    def _remember(
        self, task: TaskMessage, agent: str, result: AgentResult
    ) -> None:
        if isinstance(self.memory, NullMemoryStore):
            return
        # Skip rejected/cancelled runs — they didn't produce useful
        # outcome text. Failed runs ARE worth keeping (the agent's
        # failure summary often explains why a class of task is hard).
        if result.status not in ("success", "failed"):
            return
        # Strip any prepended memory block from the stored request so
        # future retrievals don't drift toward whatever was prepended
        # this time.
        stripped = task.natural_language.split("---\n\n", 1)[-1].strip()
        try:
            self.memory.write(
                MemoryEntry(
                    task_id=task.task_id,
                    agent=agent,
                    natural_language=stripped,
                    summary=result.summary or "",
                    status=result.status,
                    metadata={
                        "wall_seconds": result.cost.wall_seconds,
                        "total_usd": result.cost.total_usd,
                    },
                )
            )
        except Exception:
            # Memory write failures must never crash the orchestrator.
            return

    def run_plan(self, plan: Plan) -> PlanResult:
        """Execute a multi-step plan sequentially, threading prior step
        results into each subsequent step.

        Routing is bypassed: each step is pinned to a named agent
        (``PlanStep.agent``). Failure short-circuits unless the failing
        step is marked ``allow_failure=True``.

        Memory retrieval runs per-step (scoped to that step's pinned
        agent) so a long plan benefits from prior runs at each
        hop — and each completed step's outcome is written back so
        a later step (or a future plan) can pull it in.
        """
        prior: list[AgentResult] = []
        total_seconds = 0.0
        total_usd = 0.0
        terminal_status = "success"

        for i, step in enumerate(plan.steps):
            if step.agent not in self.agents:
                raise ValueError(
                    f"plan {plan.plan_id} step {i} pins unknown agent "
                    f"{step.agent!r}; registered: {list(self.agents)}"
                )
            task = step_to_task(plan, step, i, prior)
            task = self._with_memory_context(task, agent=step.agent)
            result = self._dispatch(task, step.agent)
            self._remember(task, step.agent, result)
            prior.append(result)
            total_seconds += result.cost.wall_seconds
            total_usd += result.cost.total_usd
            if result.status != "success" and not step.allow_failure:
                terminal_status = result.status
                break

        summary = (
            f"plan {plan.plan_id}: {terminal_status} "
            f"({len(prior)}/{len(plan.steps)} steps executed)"
        )
        return PlanResult(
            plan_id=plan.plan_id,
            status=terminal_status,
            summary=summary,
            step_results=prior,
            cost=CostBreakdown(total_usd=total_usd, wall_seconds=total_seconds),
        )

    def _dispatch(self, task: TaskMessage, agent_name: str) -> AgentResult:
        if agent_name not in self.agents:
            raise ValueError(
                f"unknown agent {agent_name!r}; "
                f"registered: {list(self.agents)}"
            )

        # Register the wait-event BEFORE publishing so an asynchronous
        # bus (Redis) cannot deliver the result before we are ready to
        # observe it.
        event = threading.Event()
        with self._results_lock:
            self._result_events[task.task_id] = event
            # If a result already exists (rare race on retries), surface it.
            if task.task_id in self._results:
                event.set()

        self.bus.publish(
            new_message(
                task_id=task.task_id,
                sender="orchestrator",
                recipient=agent_name,
                kind="task",
                payload=task,
            )
        )

        # Synchronous bus delivers inline (event already set); async
        # bus blocks until the consumer thread fires _on_orchestrator_msg.
        if not event.wait(timeout=self._result_timeout):
            with self._results_lock:
                self._result_events.pop(task.task_id, None)
            raise TimeoutError(
                f"agent {agent_name!r} did not produce a result for "
                f"{task.task_id!r} within {self._result_timeout:.0f}s"
            )

        with self._results_lock:
            self._result_events.pop(task.task_id, None)
            return self._results.pop(task.task_id)

    # ------------------------------------------------------------------
    # Group-chat ticket dispatch (Phase 2)
    #
    # Distinct from the router path above: ``dispatch_to`` runs a named
    # agent directly (no routing) inside a ticket, giving it a ctx bound to
    # that (ticket, agent) — a shared checkpointer for retained context plus
    # the per-ticket ask_agent resolver + dispatcher seams. The router path
    # (``run`` / ``run_plan`` / ``_dispatch``) is untouched.
    # ------------------------------------------------------------------

    def dispatch_to(
        self,
        agent_name: str,
        task: TaskMessage,
        *,
        requested_by: str = "main",
        announce: bool = True,
        with_memory: bool = False,
        aggregate_cost: bool = False,
    ) -> AgentResult:
        """Run ``agent_name`` on ``task`` within its ticket and return the
        result. When ``announce`` (the default), the dispatch and result are
        published on the bus so the ticket transcript + SSE capture them.

        ``with_memory`` prepends prior-run context (the same retrieval the
        router path uses) to the task — used for the main agent's opening
        turn so a new ticket benefits from closed ones.

        ``aggregate_cost`` (set only on the outermost call — the chat turn's
        main dispatch) replaces the returned result's ``cost`` with the sum
        across every agent that ran during the turn (main + dispatched
        specialists + ask_agent), so per-user accounting captures sub-agent
        spend. Money/tokens are summed; ``wall_seconds`` is the max, since the
        coordinator's invoke runs sub-dispatches synchronously inside its own
        wall clock (summing would double-count)."""
        ticket_id = task.ticket_id or task.task_id
        if with_memory:
            task = self._with_memory_context(task, agent=agent_name)
        # Only the outermost aggregate_cost call owns the accumulator; nested
        # dispatches just feed it via _run_in_ticket. Keyed by ticket_id so it
        # survives the ThreadPoolExecutor hop into tool calls. (A second
        # concurrent turn on the same ticket would find own_sink False and
        # report coordinator-only — acceptable; turns on one ticket are
        # sequential in practice.)
        own_sink = False
        if aggregate_cost:
            with self._ticket_lock:
                if ticket_id not in self._turn_costs:
                    self._turn_costs[ticket_id] = []
                    own_sink = True
        try:
            if announce:
                self.bus.publish(
                    new_message(
                        task_id=task.task_id,
                        sender=requested_by,
                        recipient="*",
                        kind="task",
                        payload={"to": agent_name, "subtask": task.natural_language},
                        ticket_id=ticket_id,
                    )
                )
            result = self._run_in_ticket(agent_name, task)
            if announce:
                self.bus.publish(
                    new_message(
                        task_id=task.task_id,
                        sender=agent_name,
                        recipient="*",
                        kind="result",
                        payload={
                            "agent": agent_name,
                            "status": result.status,
                            "summary": result.summary,
                            # Carry the structured artifacts too, so the actual
                            # data (e.g. a pod table in findings) reaches the
                            # transcript/UI, not just the prose summary.
                            "artifacts": result.artifacts or {},
                        },
                        ticket_id=ticket_id,
                    )
                )
            if own_sink:
                # Replace the coordinator-only cost with the whole turn's
                # cost (main + every specialist/ask_agent that ran).
                with self._ticket_lock:
                    costs = list(self._turn_costs.get(ticket_id, []))
                result = replace(result, cost=_sum_costs(costs))
            return result
        finally:
            if own_sink:
                with self._ticket_lock:
                    self._turn_costs.pop(ticket_id, None)

    def _run_in_ticket(self, agent_name: str, task: TaskMessage) -> AgentResult:
        if agent_name not in self.agents:
            raise ValueError(
                f"unknown agent {agent_name!r}; registered: {list(self.agents)}"
            )
        ticket_id = task.ticket_id or task.task_id
        key = (ticket_id, agent_name)
        with self._ticket_lock:
            if key in self._active_in_ticket:
                # Re-entrant call to an agent already on the stack — refuse
                # rather than recurse forever.
                return AgentResult(
                    task_id=task.task_id,
                    status="failed",
                    summary=(
                        f"refused: {agent_name!r} is already running in ticket "
                        f"{ticket_id!r} (recursion guard)"
                    ),
                    cost=CostBreakdown(),
                )
            self._active_in_ticket.add(key)
        # Don't even start an agent if the ticket was stopped — this is what
        # disables further auto-dispatch: the coordinator's dispatch lands here
        # and gets a cancelled result back instead of running a specialist.
        if self._cancel_event(ticket_id).is_set():
            with self._ticket_lock:
                self._active_in_ticket.discard(key)
            return AgentResult(
                task_id=task.task_id,
                status="cancelled",
                summary="Stopped by user.",
                cost=CostBreakdown(),
            )
        try:
            ctx = self._ticket_ctx(ticket_id, agent_name)
            result = self.agents[agent_name].handle(task, ctx)
            # Feed this agent's cost into the active per-turn accumulator (if
            # any) so the outermost dispatch can aggregate main + sub-agents.
            if result is not None and result.cost is not None:
                with self._ticket_lock:
                    acc = self._turn_costs.get(ticket_id)
                    if acc is not None:
                        acc.append(result.cost)
            self._emit_agent_cost(agent_name, result)
            return result
        finally:
            with self._ticket_lock:
                self._active_in_ticket.discard(key)

    def _emit_agent_cost(self, agent_name: str, result: Optional[AgentResult]) -> None:
        """Report one agent run's cost to the per-agent telemetry sink (if
        wired). Fired for every agent that runs — main and dispatched
        specialists alike — so the dashboard's by-agent breakdown reflects
        sub-agent spend. Best-effort: a sink error must never break a run."""
        sink = getattr(self.ctx, "cost_sink", None)
        if sink is None or result is None or result.cost is None:
            return
        try:
            sink(agent_name, result.cost)
        except Exception:
            logger.warning("cost_sink failed for agent %s", agent_name, exc_info=True)

    # ----- cancellation -----

    def _cancel_event(self, ticket_id: str) -> threading.Event:
        with self._ticket_lock:
            ev = self._ticket_cancels.get(ticket_id)
            if ev is None:
                ev = threading.Event()
                self._ticket_cancels[ticket_id] = ev
            return ev

    def cancel_ticket(self, ticket_id: str) -> None:
        """Stop every agent running under this ticket. Sets the cancel event;
        in-flight agents abort at their next tool / dispatch / ask_agent call,
        and no further dispatch executes."""
        self._cancel_event(ticket_id).set()

    def clear_cancel(self, ticket_id: str) -> None:
        """Reset the cancel flag — called at the start of a new turn so a prior
        Stop doesn't kill the next message."""
        self._cancel_event(ticket_id).clear()

    def is_cancelled(self, ticket_id: str) -> bool:
        return self._cancel_event(ticket_id).is_set()

    def _ticket_ctx(self, ticket_id: str, agent_name: str) -> AgentContext:
        """A ctx bound to one (ticket, agent): shared transcript, the
        per-ticket ask_agent resolver + dispatcher seams, the retained
        per-(ticket, agent) checkpointer, and the ticket's cancel event."""
        return replace(
            self.ctx,
            ticket_store=self.ticket_store,
            agent_resolver=self._make_ticket_resolver(ticket_id),
            dispatcher=self._make_ticket_dispatcher(ticket_id),
            checkpointer=self._get_ticket_checkpointer(ticket_id, agent_name),
            cancel_token=self._cancel_event(ticket_id),
        )

    def _get_ticket_checkpointer(self, ticket_id: str, agent_name: str) -> Any:
        key = (ticket_id, agent_name)
        with self._ticket_lock:
            cp = self._ticket_checkpointers.get(key)
            if cp is None:
                cp = self._checkpointer_factory()
                self._ticket_checkpointers[key] = cp
            return cp

    def _make_ticket_dispatcher(self, ticket_id: str) -> Callable[[str, str], str]:
        def dispatcher(agent_name: str, subtask: str) -> str:
            # Raise (not relay) on cancel so the coordinator's own invoke
            # unwinds instead of looping on "cancelled" dispatch results.
            if self._cancel_event(ticket_id).is_set():
                raise CancelledError("ticket stopped by user")
            task = TaskMessage(
                task_id=uuid.uuid4().hex,
                natural_language=subtask,
                ticket_id=ticket_id,
                parent_task_id=ticket_id,
            )
            return _result_relay_text(
                self.dispatch_to(agent_name, task, requested_by="main", announce=True)
            )

        return dispatcher

    def _make_ticket_resolver(self, ticket_id: str) -> Callable[[str, str], str]:
        def resolver(target_agent: str, question: str) -> str:
            if self._cancel_event(ticket_id).is_set():
                raise CancelledError("ticket stopped by user")
            if target_agent not in self.agents:
                return f"ask_agent error: unknown agent {target_agent!r}"
            task = TaskMessage(
                task_id=uuid.uuid4().hex,
                natural_language=question,
                ticket_id=ticket_id,
                parent_task_id=ticket_id,
            )
            # No announce: the ask_agent tool logs the Q&A as agent_message
            # events; re-publishing on the bus would double-log it.
            return _result_relay_text(self._run_in_ticket(target_agent, task))

        return resolver

    def discard_ticket(self, ticket_id: str) -> None:
        """Drop a ticket's retained per-agent checkpointers (called on
        ticket close — see Phase 3). Idempotent."""
        with self._ticket_lock:
            for key in [k for k in self._ticket_checkpointers if k[0] == ticket_id]:
                cp = self._ticket_checkpointers.pop(key)
                storage = getattr(cp, "storage", None)
                if storage is not None:
                    try:
                        storage.clear()
                    except Exception:
                        pass

    def close_ticket(
        self,
        ticket_id: str,
        *,
        summarizer: Optional[Callable[[list], str]] = None,
        status: str = "success",
    ) -> Optional[MemoryEntry]:
        """Close a ticket: summarize its transcript, persist the important
        details to long-term memory, and discard its per-agent checkpoints.

        The summary is written as a ``MemoryEntry`` keyed to the ticket and
        attributed to ``main`` — so a later ticket's main-agent turn
        (dispatched ``with_memory=True``) retrieves it via the existing
        memory-at-task-start path. ``summarizer`` is injectable (an LLM
        summarizer can replace the deterministic default). Returns the
        written entry, or ``None`` when there's no transcript/memory."""
        if self.ticket_store is None:
            return None
        events = self.ticket_store.transcript(ticket_id)
        summary = (summarizer or _default_ticket_summary)(events)
        request = next(
            (_event_text(e) for e in events if e.kind == "human_message" and _event_text(e)),
            "",
        )
        entry: Optional[MemoryEntry] = None
        if not isinstance(self.memory, NullMemoryStore):
            entry = MemoryEntry(
                task_id=ticket_id,
                agent="main",
                natural_language=request or "(no request recorded)",
                summary=summary,
                status=status,
                metadata={"kind": "ticket", "event_count": len(events)},
            )
            try:
                self.memory.write(entry)
            except Exception:
                # Memory write must never crash a close.
                entry = None
        self.discard_ticket(ticket_id)
        return entry


def _default_checkpointer() -> Any:
    """Lazily build an in-memory checkpoint saver. Imported here (not at
    module top) so the orchestrator stays importable without the langgraph
    stack on the pure-router path."""
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


def _sum_costs(costs: list[CostBreakdown]) -> CostBreakdown:
    """Aggregate a turn's per-agent costs. Money + tokens sum; wall_seconds
    is the max (the coordinator's invoke runs sub-dispatches synchronously
    inside its own wall clock, so summing would double-count)."""
    total = CostBreakdown()
    for c in costs:
        if c is None:
            continue
        total.total_usd += c.total_usd
        total.input_tokens += c.input_tokens
        total.output_tokens += c.output_tokens
        total.wall_seconds = max(total.wall_seconds, c.wall_seconds)
    return total


def _result_relay_text(result: AgentResult) -> str:
    """Render a sub-agent's result for relay back to the dispatcher / asker.

    The prose ``summary`` alone is lossy — specialists put the real data in
    structured ``artifacts`` (e.g. the sysadmin agent's ``findings`` holds the
    pod table while ``summary`` is a one-liner). Include the artifacts so the
    main agent actually receives the data and can act on / present it."""
    parts: list[str] = []
    if result.summary:
        parts.append(result.summary)
    artifacts = result.artifacts or {}
    # Drop empty containers so a no-artifact result stays clean.
    meaningful = {k: v for k, v in artifacts.items() if v not in (None, "", [], {})}
    if meaningful:
        try:
            parts.append(
                "Structured result:\n" + json.dumps(meaningful, indent=2, default=str)
            )
        except (TypeError, ValueError):
            parts.append(f"Structured result: {meaningful}")
    return "\n\n".join(parts).strip() or (result.summary or "")


def _event_text(ev: Any) -> str:
    """Best-effort display text from a TicketEvent payload."""
    p = ev.payload if isinstance(getattr(ev, "payload", None), dict) else {}
    for key in ("text", "summary", "answer", "question", "subtask"):
        v = p.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


def _default_ticket_summary(events: list) -> str:
    """Deterministic ticket summary: the request, which specialists were
    used, and the final main-agent outcome. No LLM — robust + cheap; swap
    in an LLM summarizer via ``close_ticket(summarizer=...)`` if desired."""
    request = next(
        (_event_text(e) for e in events if e.kind == "human_message" and _event_text(e)),
        "",
    )
    final = ""
    for e in events:
        if e.kind == "agent_message" and e.actor == "main" and _event_text(e):
            final = _event_text(e)
    dispatched = sorted({
        str((e.payload or {}).get("to"))
        for e in events
        if e.kind == "dispatch" and isinstance(e.payload, dict) and e.payload.get("to")
    })
    parts: list[str] = []
    if request:
        parts.append(f"Request: {request}")
    if dispatched:
        parts.append(f"Specialists used: {', '.join(dispatched)}")
    if final:
        parts.append(f"Outcome: {final}")
    return " | ".join(parts) or "(empty ticket)"


def _result_from_dict(d: dict) -> AgentResult:
    cost_data = d.get("cost", {})
    cost = CostBreakdown(**cost_data) if isinstance(cost_data, dict) else cost_data
    return AgentResult(**{**d, "cost": cost})
