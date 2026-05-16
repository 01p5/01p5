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
from pathlib import Path
from typing import Any, Callable, Optional

from langchain_core.tools import BaseTool, StructuredTool

from .spec import (
    AgentContext,
    AgentSpec,
    ApprovalDecision,
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
                diff=_preview_diff(inner.name, kwargs),
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

    # Pass a dict args_schema (rather than a Pydantic class) so
    # langchain's BaseTool.tool_call_schema returns it verbatim. If we
    # passed a Pydantic class, BaseTool would rebuild a "subset model"
    # that strips ``additionalProperties: false`` from the JSON schema —
    # and OpenAI's strict-mode tool calls then reject the schema.
    return StructuredTool.from_function(
        func=gated,
        name=inner.name,
        description=inner.description,
        args_schema=_strict_schema_dict(inner),
    )


def _strict_schema_dict(inner: BaseTool) -> Any:
    """Materialize the wrapped tool's args schema as a JSON-schema dict
    with ``additionalProperties: false`` set at every object level.

    Returning a dict (instead of a Pydantic class) is what makes
    BaseTool.tool_call_schema fall into its dict-passthrough branch
    and preserve our additionalProperties flag end-to-end.
    """
    if inner.args_schema is None:
        return None
    if isinstance(inner.args_schema, dict):
        return _set_additional_properties_false(dict(inner.args_schema))
    schema = inner.args_schema.model_json_schema()
    return _set_additional_properties_false(schema)


def _set_additional_properties_false(schema: dict) -> dict:
    """Recursively set ``additionalProperties: false`` on every
    JSON-schema object node. OpenAI's strict mode requires this at
    every object level."""
    if not isinstance(schema, dict):
        return schema
    if schema.get("type") == "object" or "properties" in schema:
        schema["additionalProperties"] = False
    for k in ("properties", "$defs", "definitions"):
        sub = schema.get(k)
        if isinstance(sub, dict):
            for v in sub.values():
                _set_additional_properties_false(v)
    if isinstance(schema.get("items"), dict):
        _set_additional_properties_false(schema["items"])
    if isinstance(schema.get("anyOf"), list):
        for sub in schema["anyOf"]:
            _set_additional_properties_false(sub)
    return schema


def _truncate(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…[truncated]"
    return value


# ---------------------------------------------------------------------------
# Diff preview for file-mutating tools
# ---------------------------------------------------------------------------

def _preview_diff(tool_name: str, args: dict[str, Any]) -> Optional[str]:
    """Return a unified diff for a file-mutating tool BEFORE it runs, so
    the approval card shows the reviewer exactly what's going to change.

    Two diff-able tools today:
      - write_file(path, content): diff between current file (or empty)
        and proposed content.
      - edit_file(path, old_string, new_string, replace_all): diff
        between current file and what `.replace()` would produce.

    Returns None for tools we don't know how to preview, or when the
    preview computation throws — the approval still happens, just
    without the diff hint.
    """
    import difflib
    from pathlib import Path

    try:
        if tool_name == "write_file":
            path = args.get("path", "")
            new = args.get("content", "")
            old = ""
            try:
                p = Path(path).expanduser()
                if p.is_file():
                    old = p.read_text()
            except Exception:
                pass
            return _unified_diff(old, new, path)
        if tool_name == "edit_file":
            path = args.get("path", "")
            old_string = args.get("old_string", "")
            new_string = args.get("new_string", "")
            replace_all = bool(args.get("replace_all", False))
            try:
                current = Path(path).expanduser().read_text()
            except Exception:
                return None
            proposed = (
                current.replace(old_string, new_string)
                if replace_all
                else current.replace(old_string, new_string, 1)
            )
            return _unified_diff(current, proposed, path)
    except Exception:
        return None
    return None


def _unified_diff(old: str, new: str, path: str) -> str:
    import difflib
    diff_lines = list(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    ))
    if not diff_lines:
        return "(no textual difference)"
    return "".join(diff_lines)


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
