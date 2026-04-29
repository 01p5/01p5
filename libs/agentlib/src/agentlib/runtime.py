"""
Runtime support for AgentSpec: tool-gating and approval interception.

The key invariant: an agent cannot invoke a tool outside its declared
``tools`` set, and any tool whose name is in ``destructive_verbs`` is
routed through the ApprovalHook before execution. Both checks happen in
the runtime, not in the prompt, so a prompt-injected agent still cannot
escalate.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Optional

from langchain.tools import BaseTool, StructuredTool

from .spec import (
    AgentContext,
    AgentSpec,
    ApprovalDecision,
    ApprovalHook,
    AuditLogger,
)


class ToolGateError(RuntimeError):
    """Raised when the runtime detects a violation of the tool contract."""


def _tool_name(t: BaseTool | Callable) -> str:
    if isinstance(t, BaseTool):
        return t.name
    return getattr(t, "name", t.__name__)


def gate_tools(
    spec: AgentSpec,
    ctx: AgentContext,
    task_id: str,
) -> list[BaseTool]:
    """Wrap every tool in ``spec.tools`` so the runtime can:

    - Confirm the call is for a declared tool (defense-in-depth — LangChain
      already filters, but we don't trust the framework alone).
    - Intercept calls to destructive tools and route through ``ctx.approval``.
    - Append every call (approved, rejected, or non-destructive) to the audit log.
    """
    declared = {_tool_name(t) for t in spec.tools}
    wrapped: list[BaseTool] = []

    for tool in spec.tools:
        base = tool if isinstance(tool, BaseTool) else _as_structured(tool)
        if base.name not in declared:
            raise ToolGateError(
                f"tool {base.name!r} is not in {spec.name}.tools — "
                "this is a programming error in the agent definition"
            )
        wrapped.append(_wrap_one(base, spec, ctx, task_id))
    return wrapped


def _as_structured(fn: Callable) -> StructuredTool:
    return StructuredTool.from_function(fn)


def _wrap_one(
    inner: BaseTool,
    spec: AgentSpec,
    ctx: AgentContext,
    task_id: str,
) -> BaseTool:
    is_destructive = inner.name in spec.destructive_verbs
    audit = ctx.audit
    approval = ctx.approval

    def gated(**kwargs: Any) -> Any:
        if is_destructive:
            decision = approval.request(
                agent=spec.name,
                tool=inner.name,
                args=kwargs,
                rationale=f"{spec.name} requesting {inner.name}",
            )
            audit.log_tool_call(
                task_id=task_id,
                agent=spec.name,
                tool=inner.name,
                args=kwargs,
                result=None,
                approved=decision.approved,
            )
            if not decision.approved:
                return f"REJECTED by human: {decision.reason}"
            if decision.modified_args is not None:
                kwargs = decision.modified_args
        result = inner.invoke(kwargs)
        if not is_destructive:
            audit.log_tool_call(
                task_id=task_id,
                agent=spec.name,
                tool=inner.name,
                args=kwargs,
                result=_truncate(result),
                approved=None,
            )
        else:
            audit.log_tool_call(
                task_id=task_id,
                agent=spec.name,
                tool=inner.name,
                args=kwargs,
                result=_truncate(result),
                approved=True,
            )
        return result

    return StructuredTool.from_function(
        func=gated,
        name=inner.name,
        description=inner.description,
        args_schema=inner.args_schema,
    )


def _truncate(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…[truncated]"
    return value


# ---------------------------------------------------------------------------
# Built-in implementations of ApprovalHook and AuditLogger
# ---------------------------------------------------------------------------

class ConsoleApprovalHook:
    """Synchronous CLI approval — prompts on stdin. PoC-grade."""

    def request(
        self,
        agent: str,
        tool: str,
        args: dict[str, Any],
        rationale: str,
        diff: Optional[str] = None,
    ) -> ApprovalDecision:
        print(f"\n[approval] {agent} → {tool}")
        print(f"  args: {json.dumps(args, indent=2, default=str)}")
        print(f"  rationale: {rationale}")
        if diff:
            print(f"  diff:\n{diff}")
        ans = input("approve? [y/N]: ").strip().lower()
        if ans == "y":
            return ApprovalDecision(approved=True, reason="approved via CLI")
        return ApprovalDecision(approved=False, reason="rejected via CLI")


class AlwaysApprove:
    """For tests only."""

    def request(self, **kwargs: Any) -> ApprovalDecision:
        return ApprovalDecision(approved=True, reason="auto-approved (test)")


class AlwaysReject:
    """For tests only."""

    def request(self, **kwargs: Any) -> ApprovalDecision:
        return ApprovalDecision(approved=False, reason="auto-rejected (test)")


class JsonlAuditLogger:
    """Append-only JSONL audit log. Every tool call lands here."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_tool_call(
        self,
        task_id: str,
        agent: str,
        tool: str,
        args: dict[str, Any],
        result: Any,
        approved: Optional[bool],
    ) -> None:
        record = {
            "ts": time.time(),
            "task_id": task_id,
            "agent": agent,
            "tool": tool,
            "args": args,
            "result": result if isinstance(result, (str, int, float, bool, type(None))) else str(result),
            "approved": approved,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


class InMemoryAuditLogger:
    """For tests."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def log_tool_call(
        self,
        task_id: str,
        agent: str,
        tool: str,
        args: dict[str, Any],
        result: Any,
        approved: Optional[bool],
    ) -> None:
        self.records.append(
            {
                "task_id": task_id,
                "agent": agent,
                "tool": tool,
                "args": args,
                "result": result,
                "approved": approved,
            }
        )
