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

from langchain.tools import BaseTool


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
    """Runtime-injected. Agents should not construct this directly."""
    approval: ApprovalHook
    audit: AuditLogger
    secrets: Optional[Any] = None  # vault client; not implemented in v0
    cancel_token: Optional[Any] = None


class AgentSpec(ABC):
    """Base class for every Olympus agent.

    Concrete agents declare ``name``, ``domain``, ``tools``, and
    ``destructive_verbs`` as class attributes, then implement ``handle``.
    The runtime wraps tools to enforce gating before ``handle`` runs.
    """
    name: str
    domain: str
    tools: Sequence[BaseTool | Callable]
    destructive_verbs: set[str] = set()
    model: str = ""

    @abstractmethod
    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult: ...
