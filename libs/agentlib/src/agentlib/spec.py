"""
AgentSpec — the contract every Olympus agent implements.

See docs/AGENT_SPEC.md for the design rationale. This module provides the
base dataclasses and the AgentSpec abstract class. The runtime that
enforces tool-gating and routes destructive calls through the approval
hook lives in agentlib.runtime.

v0 note: synchronous. Promote to async in v1 once the bus exists.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional, Protocol, Sequence

from langchain_core.tools import BaseTool


@dataclass
class TaskMessage:
    task_id: str
    natural_language: str
    inputs: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    parent_task_id: Optional[str] = None
    history_ref: Optional[str] = None


@dataclass
class CostBreakdown:
    total_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    wall_seconds: float = 0.0


@dataclass
class AgentResult:
    task_id: str
    status: Literal["success", "failed", "rejected", "cancelled"]
    summary: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    cost: CostBreakdown = field(default_factory=CostBreakdown)
    transcript_ref: Optional[str] = None


@dataclass
class ApprovalDecision:
    approved: bool
    reason: str
    modified_args: Optional[dict[str, Any]] = None


class ApprovalHook(Protocol):
    def request(
        self,
        agent: str,
        tool: str,
        args: dict[str, Any],
        rationale: str,
        diff: Optional[str] = None,
    ) -> ApprovalDecision: ...


class AuditLogger(Protocol):
    def log_tool_call(
        self,
        task_id: str,
        agent: str,
        tool: str,
        args: dict[str, Any],
        result: Any,
        approved: Optional[bool],
    ) -> None: ...


@dataclass
class AgentContext:
    """Runtime-injected. Agents should not construct this directly.

    ``rollback`` is an optional ``RollbackStore`` (Protocol defined in
    ``agentlib.rollback``). Passed in as ``Any`` here to avoid a
    circular import — the rollback module imports nothing from spec
    so the only place to wire the dependency is through the Context."""
    approval: ApprovalHook
    audit: AuditLogger
    secrets: Optional[Any] = None  # vault client; not implemented in v0
    cancel_token: Optional[Any] = None
    rollback: Optional[Any] = None  # RollbackStore | None


class AgentSpec(ABC):
    """Base class for every Olympus agent.

    Concrete agents declare ``name``, ``domain``, ``tools``, and
    ``destructive_verbs`` as class attributes, then implement ``handle``.
    The runtime wraps tools to enforce gating before ``handle`` runs.

    ``rollback_snapshots`` maps a destructive tool name to a callable
    that, given the tool args, returns a ``RollbackPlan`` describing
    how to undo the operation. Snapshot fns run *after* approval and
    *before* the forward tool executes — so they see the pre-state.
    Agents that don't declare a snapshot for a verb simply opt out of
    rollback for that verb (no error, just no undo).
    """
    name: str
    domain: str
    tools: Sequence[BaseTool | Callable]
    destructive_verbs: set[str] = set()
    rollback_snapshots: dict[str, Callable[[dict], Any]] = {}
    model: str = ""

    @abstractmethod
    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult: ...
