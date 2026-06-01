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
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from langchain_core.tools import BaseTool, StructuredTool

from .spec import (
    AgentContext,
    AgentSpec,
    ApprovalDecision,
)

logger = logging.getLogger(__name__)


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
    ticket_id: Optional[str] = None,
    extra_tools: Optional[list[Any]] = None,
) -> list[BaseTool]:
    """Wrap every tool in ``spec.tools`` so the runtime can:

    - Confirm the call is for a declared tool (defense-in-depth — LangChain
      already filters, but we don't trust the framework alone).
    - Intercept calls to destructive tools and route through ``ctx.approval``.
    - Append every call (approved, rejected, or non-destructive) to the audit log.
    - Record a ``tool_call`` event on the ticket transcript (when
      ``ctx.ticket_store`` is set).

    When ``ctx.agent_resolver`` is set, the read-only ``ask_agent`` tool is
    appended so the agent can ask sibling participants directed questions.
    ``ticket_id`` falls back to ``task_id`` for standalone tasks.

    ``extra_tools`` allows the agent's handle() to inject context-bound
    tools (e.g. closures over ``ctx.inventory_store``) without mutating
    the AgentSpec at class scope. They go through the same wrapping
    pipeline; their destructive-or-not classification still consults
    ``spec.destructive_verbs`` by name, so the agent must declare them
    there statically if they should be gated."""
    extras = list(extra_tools or [])
    all_tools = list(spec.tools) + extras
    declared = {_tool_name(t) for t in all_tools}
    wrapped: list[BaseTool] = []

    effective_ticket_id = ticket_id or task_id
    ticket_store = getattr(ctx, "ticket_store", None)

    for tool in all_tools:
        base = tool if isinstance(tool, BaseTool) else _as_structured(tool)
        if base.name not in declared:
            raise ToolGateError(
                f"tool {base.name!r} is not in {spec.name}.tools — "
                "this is a programming error in the agent definition"
            )
        wrapped.append(
            _wrap_one(base, spec, ctx, task_id, effective_ticket_id,
                      ticket_store, approval_ticket_id=ticket_id)
        )

    resolver = getattr(ctx, "agent_resolver", None)
    if resolver is not None:
        from .ticket import make_ask_agent_tool

        wrapped.append(
            make_ask_agent_tool(
                asker=spec.name,
                resolver=resolver,
                ticket_id=effective_ticket_id,
                ticket_store=ticket_store,
            )
        )
    return wrapped


def _as_structured(fn: Callable) -> StructuredTool:
    return StructuredTool.from_function(fn)


def _wrap_one(
    inner: BaseTool,
    spec: AgentSpec,
    ctx: AgentContext,
    task_id: str,
    ticket_id: Optional[str] = None,
    ticket_store: Optional[Any] = None,
    *,
    approval_ticket_id: Optional[str] = None,
) -> BaseTool:
    """``ticket_id`` is the *effective* ticket id used for the
    transcript projection (falls back to task_id for standalone runs so
    the ticket store still has a key). ``approval_ticket_id`` is the
    *original* ticket id passed to gate_tools — None when there's no
    real ticket — so the dashboard's /approvals can distinguish
    "approval inside a group chat" from "approval for a standalone
    /tasks run" and the chat-page filter / toast suppression works
    correctly."""
    is_destructive = inner.name in spec.destructive_verbs
    audit = ctx.audit
    approval = ctx.approval
    # Self-protection: hard-deny calls that target the cluster / VM hosts
    # Olympus runs on. Checked BEFORE approval so a malicious user can't
    # approve their own self-escalation. None / disabled => no-op.
    policy = getattr(ctx, "self_protection", None)
    inventory_store = getattr(ctx, "inventory_store", None)

    snapshot_fn = (
        spec.rollback_snapshots.get(inner.name)
        if is_destructive and getattr(spec, "rollback_snapshots", None)
        else None
    )

    def emit_tool_call(args: dict[str, Any], result: Any, approved: Optional[bool]) -> None:
        """Mirror the audit record onto the ticket transcript. Best-effort —
        a transcript hiccup must never break a tool call."""
        if ticket_store is None:
            return
        try:
            from .ticket import TicketEvent

            ticket_store.append(
                TicketEvent(
                    ticket_id=ticket_id or task_id,
                    actor=spec.name,
                    kind="tool_call",
                    payload={
                        "tool": inner.name,
                        "args": args,
                        "result": result,
                        "approved": approved,
                    },
                    task_id=task_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "ticket tool_call emit failed for %s.%s: %s",
                spec.name, inner.name, exc,
            )

    def gated(**kwargs: Any) -> Any:
        # Self-protection runs first — for destructive AND read-ish tools —
        # and hard-denies without ever reaching the approval hook or the
        # underlying tool. The deny is audited (approved=False) + mirrored
        # to the ticket transcript so the attempt is visible.
        if policy is not None and getattr(policy, "enabled", False):
            deny = policy.check(inner.name, kwargs, inventory_store=inventory_store)
            if deny is not None:
                audit.log_tool_call(
                    task_id=task_id,
                    agent=spec.name,
                    tool=inner.name,
                    args=kwargs,
                    result=deny,
                    approved=False,
                )
                emit_tool_call(kwargs, deny, False)
                return deny
        if is_destructive:
            decision = approval.request(
                agent=spec.name,
                tool=inner.name,
                args=kwargs,
                rationale=f"{spec.name} requesting {inner.name}",
                diff=_preview_diff(inner.name, kwargs),
                # Thread the *original* ticket id so the dashboard's
                # /approvals response carries None for standalone /tasks
                # runs and the real ticket id for group-chat dispatches.
                # The chat page uses this to render the card inline; the
                # global toast broker uses it to suppress the popup when
                # the user is already on /chat/{ticket_id}.
                ticket_id=approval_ticket_id,
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
                emit_tool_call(kwargs, None, decision.approved)
                return f"REJECTED by human: {decision.reason}"
            if decision.modified_args is not None:
                kwargs = decision.modified_args

            # Snapshot pre-state *before* the destructive call fires.
            # A failed snapshot must not block the forward call —
            # rollback is opt-in convenience, not a safety guarantee.
            rollback_plan = None
            if snapshot_fn is not None:
                try:
                    rollback_plan = snapshot_fn(dict(kwargs))
                except Exception as exc:
                    logger.warning(
                        "rollback snapshot failed for %s.%s: %s",
                        spec.name, inner.name, exc,
                    )

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
            emit_tool_call(kwargs, _truncate(result), None)
        else:
            audit.log_tool_call(
                task_id=task_id,
                agent=spec.name,
                tool=inner.name,
                args=kwargs,
                result=_truncate(result),
                approved=True,
            )
            emit_tool_call(kwargs, _truncate(result), True)
            # Persist the captured plan only if the forward call
            # succeeded (no exception). Tools that signal failure by
            # returning an error string still trigger persistence —
            # the rollback entry's snapshot makes that distinguishable
            # from a true success, and a human can decide.
            store = getattr(ctx, "rollback", None)
            if rollback_plan is not None and store is not None:
                try:
                    from .rollback import plan_to_entry

                    store.write(
                        plan_to_entry(
                            rollback_plan,
                            task_id=task_id,
                            agent=spec.name,
                            forward_tool=inner.name,
                            forward_args=dict(kwargs),
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "rollback persist failed for %s.%s: %s",
                        spec.name, inner.name, exc,
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
        *,
        ticket_id: Optional[str] = None,  # noqa: ARG002 — CLI doesn't surface this
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
