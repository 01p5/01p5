"""
Olympus dashboard backend.

Stdlib HTTP server. Same family of decisions as ``WebhookApprovalHook``:
no FastAPI/Flask. The bus is the source of truth for live activity;
the dashboard is a thin SSE bridge over it plus the approval queue and
the direct-tool-invocation endpoints.

Endpoints
---------

LLM-driven (agent picks the tools):

- ``GET /``                       — static index.html (the UI).
- ``POST /tasks``                 — body: ``{natural_language, router?}``,
                                    returns ``{task_id}``.
- ``GET /tasks``                  — list known task ids + status.
- ``GET /tasks/{id}``             — final result (``404`` if unknown,
                                    ``202`` while in flight).
- ``GET /tasks/{id}/events``      — SSE stream of bus messages for the
                                    task.

Live activity + audit:

- ``GET /events``                 — SSE stream of every bus message.
- ``GET /audit``                  — JSONL audit log download.
- ``GET /healthz``                — liveness check.

Human approval queue (also used by the LLM-driven path):

- ``GET /approvals``              — list pending approvals.
- ``POST /approvals/{id}``        — body: ``{approved, reason,
                                    modified_args?}``. Resolves a
                                    pending approval.

Human-driven tool invocation (no LLM in the loop, same gating + audit):

- ``GET /tools``                  — catalog: every tool, the agent it
                                    belongs to, its args JSON schema,
                                    and whether it is destructive.
- ``POST /tools/{agent}/{tool}``  — body: tool args dict, returns
                                    ``{result}``. Synchronously blocks
                                    until tool returns (or until the
                                    human resolves the approval queue
                                    card, for destructive tools).
- ``GET /stacks/terraform``       — list known terraform stacks
                                    (subdirs of infra/terraform/
                                    containing *.tf), to feed into
                                    tf_plan/tf_apply args.
- ``GET /stacks/ansible``         — list known ansible playbooks
                                    (top-level *.yml under infra/ansible/).

This module is import-safe even when the LLM stack and the agent
packages are not installed — the orchestrator + agents are passed in by
the caller.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from agentlib import (
    AgentContext,
    Bus,
    BusMessage,
    EmbeddingMemoryStore,
    InMemoryBus,
    JsonlAuditLogger,
    JsonlMemoryStore,
    JsonlRollbackStore,
    MemoryStore,
    Orchestrator,
    QueueApprovalHook,
    RollbackStore,
    Router,
    TaskMessage,
    gate_tools,
)
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

# Default location for the JSONL audit log. Mirrors the per-agent CLIs.
DEFAULT_AUDIT_LOG = str(Path("~/.olympus/audit.jsonl").expanduser())


@dataclasses.dataclass
class TaskRecord:
    """In-memory state per submitted task. The bus is the source of
    truth for messages; this record is the orchestrator-result cache."""
    task_id: str
    natural_language: str
    submitted_at: float
    status: str = "pending"  # pending → running → success / failed / cancelled
    result_summary: Optional[str] = None
    result_artifacts: Optional[dict] = None
    error: Optional[str] = None
    # Per-task cost from the agent's CostBreakdown. Populated when the
    # orchestrator's "result" lands on the bus. None until then.
    cost_usd: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    wall_seconds: Optional[float] = None
    agent: Optional[str] = None


class DashboardServer:
    """Owns the orchestrator + bus + approval queue + HTTP server."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        bus: Bus,
        approval_hook: QueueApprovalHook,
        audit_log_path: str = DEFAULT_AUDIT_LOG,
        host: str = "127.0.0.1",
        port: int = 8765,
        static_dir: Optional[Path] = None,
        mcp_servers: Optional[list[dict[str, Any]]] = None,
    ):
        self.orchestrator = orchestrator
        self.bus = bus
        self.approval_hook = approval_hook
        self.audit_log_path = audit_log_path
        # MCP server registry: list of dicts with the per-server view
        # the UI needs (name, target_agent, tools, destructive set,
        # config-summary). Populated by build_default_server when
        # mcp_servers are wired at startup; runtime add/remove is a
        # follow-up.
        self.mcp_servers: list[dict[str, Any]] = list(mcp_servers or [])
        # Static dir resolution: the Vite-built SPA at static/dist/ is
        # the preferred source. Fall back to legacy static/ if dist/
        # doesn't exist (e.g. dev test without a frontend build).
        if static_dir is not None:
            self.static_dir = static_dir
        else:
            base = Path(__file__).resolve().parent.parent.parent / "static"
            self.static_dir = base / "dist" if (base / "dist").is_dir() else base

        self._tasks: dict[str, TaskRecord] = {}
        self._tasks_lock = threading.Lock()

        # Subscribe an internal sink to mark tasks as completed when the
        # orchestrator's "result" message lands on the bus.
        self.bus.subscribe("orchestrator", self._on_orchestrator_msg)

        self._server: Optional[ThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._host = host
        self._port = port

    # ---- internal sinks ----

    def _on_orchestrator_msg(self, msg: BusMessage) -> None:
        if msg.kind != "result":
            return
        payload = msg.payload
        with self._tasks_lock:
            rec = self._tasks.get(msg.task_id)
            if rec is None:
                return
            if isinstance(payload, dict):
                rec.status = payload.get("status", "success")
                rec.result_summary = payload.get("summary")
                rec.result_artifacts = payload.get("artifacts")
                cost = payload.get("cost") or {}
                if isinstance(cost, dict):
                    rec.cost_usd = cost.get("total_usd")
                    rec.input_tokens = cost.get("input_tokens")
                    rec.output_tokens = cost.get("output_tokens")
                    rec.wall_seconds = cost.get("wall_seconds")
            else:
                rec.status = getattr(payload, "status", "success")
                rec.result_summary = getattr(payload, "summary", None)
                rec.result_artifacts = getattr(payload, "artifacts", None)
                cost = getattr(payload, "cost", None)
                if cost is not None:
                    rec.cost_usd = getattr(cost, "total_usd", None)
                    rec.input_tokens = getattr(cost, "input_tokens", None)
                    rec.output_tokens = getattr(cost, "output_tokens", None)
                    rec.wall_seconds = getattr(cost, "wall_seconds", None)
            # Sender of the "result" message is the agent that handled it.
            rec.agent = msg.sender or rec.agent

    # ---- task submission (worker thread) ----

    def submit(self, natural_language: str) -> str:
        task_id = str(uuid.uuid4())
        rec = TaskRecord(
            task_id=task_id,
            natural_language=natural_language,
            submitted_at=time.time(),
        )
        with self._tasks_lock:
            self._tasks[task_id] = rec

        def worker():
            rec.status = "running"
            try:
                self.orchestrator.run(
                    TaskMessage(task_id=task_id, natural_language=natural_language)
                )
            except Exception as exc:
                logger.exception("task %s failed", task_id)
                with self._tasks_lock:
                    rec.status = "failed"
                    rec.error = f"{type(exc).__name__}: {exc}"

        threading.Thread(
            target=worker, name=f"dashboard-task:{task_id}", daemon=True
        ).start()
        return task_id

    # ---- HTTP server lifecycle ----

    def serve(self) -> None:
        self._server = ThreadingHTTPServer(
            (self._host, self._port), self._make_handler()
        )
        host, port = self._server.server_address[:2]
        self._host, self._port = host, port
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="dashboard-http",
            daemon=True,
        )
        self._server_thread.start()
        logger.info("Olympus dashboard listening on http://%s:%s", host, port)

    @property
    def address(self) -> tuple[str, int]:
        return (self._host, self._port)

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._server_thread is not None and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)

    def __enter__(self) -> "DashboardServer":
        self.serve()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()

    # ---- handler factory ----

    def _make_handler(self):
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                logger.debug("dashboard %s - %s", self.address_string(), fmt % args)

            # ----- routing -----

            def do_GET(self):  # noqa: N802
                path, _, _query = self.path.partition("?")
                if path == "/" or path == "/index.html":
                    return outer._serve_static(self, "index.html")
                if path == "/healthz":
                    return outer._send_json(self, 200, {"ok": True})
                if path == "/tasks":
                    return outer._handle_list_tasks(self)
                if path.startswith("/tasks/"):
                    rest = path[len("/tasks/"):]
                    if rest.endswith("/events"):
                        return outer._handle_task_events(self, rest[: -len("/events")])
                    return outer._handle_get_task(self, rest)
                if path == "/events":
                    return outer._handle_all_events(self)
                if path == "/approvals":
                    return outer._handle_list_approvals(self)
                if path == "/audit":
                    return outer._serve_static_file(
                        self, Path(outer.audit_log_path),
                        content_type="application/x-ndjson",
                        allow_missing=True,
                    )
                if path == "/tools":
                    return outer._handle_list_tools(self)
                if path == "/memory":
                    return outer._handle_list_memory(self)
                if path == "/rollback":
                    return outer._handle_list_rollbacks(self)
                if path == "/telemetry":
                    return outer._handle_telemetry(self)
                if path == "/mcp/servers":
                    return outer._handle_list_mcp_servers(self)
                if path.startswith("/mcp/servers/") and path.endswith("/tools"):
                    inner = path[len("/mcp/servers/"):-len("/tools")]
                    return outer._handle_list_mcp_tools(self, inner)
                if path == "/stacks/terraform":
                    return outer._handle_list_terraform_stacks(self)
                if path == "/stacks/ansible":
                    return outer._handle_list_ansible_playbooks(self)
                if path.startswith("/static/"):
                    return outer._serve_static(self, path[len("/static/"):])
                # Vite-built hashed assets live under /assets/.
                if path.startswith("/assets/"):
                    return outer._serve_static(self, path.lstrip("/"))
                # Top-level static files Vite may emit (favicon, vite.svg, etc).
                if path in ("/favicon.svg", "/favicon.ico", "/vite.svg"):
                    return outer._serve_static(self, path.lstrip("/"))
                # SPA fallback — any unmatched GET serves index.html so
                # client-side routes (/chat, /kubernetes, /terraform, …)
                # work on a hard refresh.
                return outer._serve_static(self, "index.html")

            def do_POST(self):  # noqa: N802
                if self.path == "/tasks":
                    return outer._handle_post_task(self)
                if self.path.startswith("/approvals/"):
                    return outer._handle_resolve_approval(
                        self, self.path[len("/approvals/"):]
                    )
                if self.path.startswith("/memory/") and self.path.endswith("/feedback"):
                    inner = self.path[len("/memory/"):-len("/feedback")]
                    return outer._handle_memory_feedback(self, inner)
                if self.path.startswith("/rollback/") and self.path.endswith("/execute"):
                    inner = self.path[len("/rollback/"):-len("/execute")]
                    return outer._handle_execute_rollback(self, inner)
                if self.path.startswith("/tools/"):
                    rest = self.path[len("/tools/"):]
                    if "/" in rest:
                        agent_name, _, tool_name = rest.partition("/")
                        return outer._handle_invoke_tool(self, agent_name, tool_name)
                self.send_response(404)
                self.end_headers()

        return _Handler

    # ---- handler implementations ----

    @staticmethod
    def _send_json(req: BaseHTTPRequestHandler, status: int, body: Any) -> None:
        encoded = json.dumps(body, default=str).encode("utf-8")
        req.send_response(status)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(encoded)))
        req.end_headers()
        req.wfile.write(encoded)

    @staticmethod
    def _read_json(req: BaseHTTPRequestHandler) -> Any:
        length = int(req.headers.get("Content-Length", "0") or 0)
        return json.loads(req.rfile.read(length) or b"{}")

    def _serve_static(self, req: BaseHTTPRequestHandler, name: str) -> None:
        path = (self.static_dir / name).resolve()
        # Path-traversal defense: requested path must be inside static_dir.
        try:
            path.relative_to(self.static_dir.resolve())
        except ValueError:
            req.send_response(404)
            req.end_headers()
            return
        if not path.is_file():
            req.send_response(404)
            req.end_headers()
            return
        ext = path.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
        }.get(ext, "application/octet-stream")
        self._serve_static_file(req, path, content_type=ctype, allow_missing=False)

    @staticmethod
    def _serve_static_file(
        req: BaseHTTPRequestHandler,
        path: Path,
        content_type: str,
        allow_missing: bool = False,
    ) -> None:
        if not path.is_file():
            if allow_missing:
                req.send_response(200)
                req.send_header("Content-Type", content_type)
                req.send_header("Content-Length", "0")
                req.end_headers()
                return
            req.send_response(404)
            req.end_headers()
            return
        data = path.read_bytes()
        req.send_response(200)
        req.send_header("Content-Type", content_type)
        req.send_header("Content-Length", str(len(data)))
        req.end_headers()
        req.wfile.write(data)

    def _handle_post_task(self, req: BaseHTTPRequestHandler) -> None:
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            self._send_json(req, 400, {"error": "invalid JSON"})
            return
        nl = body.get("natural_language")
        if not isinstance(nl, str) or not nl.strip():
            self._send_json(req, 400, {"error": "natural_language required"})
            return
        task_id = self.submit(nl.strip())
        self._send_json(req, 202, {"task_id": task_id})

    def _handle_list_tasks(self, req: BaseHTTPRequestHandler) -> None:
        with self._tasks_lock:
            payload = [dataclasses.asdict(t) for t in self._tasks.values()]
        self._send_json(req, 200, payload)

    def _handle_get_task(self, req: BaseHTTPRequestHandler, task_id: str) -> None:
        with self._tasks_lock:
            rec = self._tasks.get(task_id)
        if rec is None:
            self._send_json(req, 404, {"error": "unknown task"})
            return
        if rec.status in ("pending", "running"):
            self._send_json(req, 202, dataclasses.asdict(rec))
            return
        self._send_json(req, 200, dataclasses.asdict(rec))

    def _handle_list_approvals(self, req: BaseHTTPRequestHandler) -> None:
        items = [
            {
                "approval_id": a.approval_id,
                "agent": a.agent,
                "tool": a.tool,
                "args": a.args,
                "rationale": a.rationale,
                "diff": a.diff,
                "requested_at": a.requested_at,
            }
            for a in self.approval_hook.pending()
        ]
        self._send_json(req, 200, items)

    def _handle_resolve_approval(
        self, req: BaseHTTPRequestHandler, approval_id: str
    ) -> None:
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            self._send_json(req, 400, {"error": "invalid JSON"})
            return
        approved = bool(body.get("approved", False))
        reason = str(body.get("reason", "no reason given"))
        modified = body.get("modified_args")
        if modified is not None and not isinstance(modified, dict):
            self._send_json(req, 400, {"error": "modified_args must be an object"})
            return
        ok = self.approval_hook.resolve(
            approval_id, approved=approved, reason=reason, modified_args=modified
        )
        if not ok:
            self._send_json(req, 404, {"error": "unknown or already-resolved approval"})
            return
        self._send_json(req, 200, {"resolved": approval_id})

    # ---- Human-driven tool invocation (no LLM) ----

    def _handle_list_tools(self, req: BaseHTTPRequestHandler) -> None:
        """Catalog every tool every registered agent exposes.

        Returned schema is exactly what the UI needs to build a form
        per tool: name, description, JSON schema for args, and the
        destructive flag (so the UI can warn before submit).
        """
        out: list[dict] = []
        for agent_name, agent in self.orchestrator.agents.items():
            destructive = set(agent.destructive_verbs or set())
            for raw_tool in agent.tools:
                tool = _as_base_tool(raw_tool)
                schema = _tool_args_schema(tool)
                out.append({
                    "agent": agent_name,
                    "name": tool.name,
                    "description": tool.description or "",
                    "args_schema": schema,
                    "destructive": tool.name in destructive,
                })
        self._send_json(req, 200, out)

    def _handle_invoke_tool(
        self, req: BaseHTTPRequestHandler, agent_name: str, tool_name: str
    ) -> None:
        """Invoke a single tool directly. Same gate_tools wrapping the
        LLM-driven path uses — destructive tools still surface the
        approval card and block until resolved."""
        try:
            args = self._read_json(req)
        except json.JSONDecodeError:
            self._send_json(req, 400, {"error": "invalid JSON"})
            return
        if not isinstance(args, dict):
            self._send_json(req, 400, {"error": "body must be an object"})
            return
        agent = self.orchestrator.agents.get(agent_name)
        if agent is None:
            self._send_json(req, 404, {"error": f"unknown agent {agent_name!r}"})
            return
        # Synthetic task id — keeps the audit log + approval queue stamps
        # honest about where the invocation came from.
        task_id = f"manual:{uuid.uuid4()}"
        try:
            gated = gate_tools(agent, self.orchestrator.ctx, task_id)
        except Exception as exc:
            logger.exception("gate_tools failed for %s.%s", agent_name, tool_name)
            self._send_json(req, 500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        target = next((t for t in gated if t.name == tool_name), None)
        if target is None:
            self._send_json(req, 404, {"error": f"unknown tool {tool_name!r} on agent {agent_name!r}"})
            return
        # Optionally publish a bus event so the live feed shows the
        # human-driven invocation too.
        try:
            from agentlib import new_message
            self.bus.publish(new_message(
                task_id=task_id, sender="human", recipient=agent_name,
                kind="task",
                payload={"natural_language": f"[direct] {tool_name}({args})", "inputs": args},
            ))
        except Exception:
            pass  # bus publish is best-effort for UI feedback
        try:
            result = target.invoke(args)
        except Exception as exc:
            logger.exception("tool invocation %s.%s failed", agent_name, tool_name)
            self._send_json(req, 500, {
                "task_id": task_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
            return
        # Mirror the result back on the bus for the live feed.
        try:
            from agentlib import new_message
            self.bus.publish(new_message(
                task_id=task_id, sender=agent_name, recipient="human",
                kind="result",
                payload={"status": "success", "summary": str(result)[:500]},
            ))
        except Exception:
            pass
        self._send_json(req, 200, {
            "task_id": task_id,
            "agent": agent_name,
            "tool": tool_name,
            "result": result if isinstance(result, (str, int, float, bool, type(None), list, dict)) else str(result),
        })

    def _handle_list_rollbacks(self, req: BaseHTTPRequestHandler) -> None:
        """List rollback entries.

        Query string:
          - ``?task_id=<id>`` → entries for that task
          - (none)            → recent entries (newest first), bounded
                                by ``k`` (default 25, max 100)."""
        from urllib.parse import parse_qs, urlparse

        store = getattr(self.orchestrator.ctx, "rollback", None)
        if store is None:
            return self._send_json(req, 200, {"entries": []})

        params = parse_qs(urlparse(req.path).query)
        task_id = (params.get("task_id") or [None])[0]
        try:
            k = max(1, min(int((params.get("k") or ["25"])[0]), 100))
        except ValueError:
            k = 25

        try:
            if task_id:
                entries = store.list_for_task(task_id)
            else:
                entries = store.list_recent(k=k)
        except Exception as exc:
            logger.warning("rollback list failed: %s", exc)
            entries = []

        payload = [self._rollback_to_dict(e) for e in entries[:k]]
        self._send_json(req, 200, {"entries": payload})

    def _handle_execute_rollback(
        self, req: BaseHTTPRequestHandler, rollback_id: str
    ) -> None:
        """Execute a captured rollback by invoking its inverse tool.

        The inverse fires through the same ``gate_tools`` machinery as
        any human-driven tool invocation — so the user re-approves
        before the undo lands. On success, marks the entry executed
        in the store so a second click is a no-op (UI can grey out
        the button)."""
        store = getattr(self.orchestrator.ctx, "rollback", None)
        if store is None:
            return self._send_json(
                req, 409, {"error": "rollback store not configured"}
            )
        try:
            entry = store.get(rollback_id)
        except Exception as exc:
            logger.warning("rollback get failed: %s", exc)
            entry = None
        if entry is None:
            return self._send_json(
                req, 404,
                {"error": f"no rollback entry {rollback_id!r}"},
            )
        if entry.executed:
            return self._send_json(
                req, 409,
                {
                    "error": "rollback already executed",
                    "executed_ts": entry.executed_ts,
                    "executed_result": entry.executed_result,
                },
            )
        agent = self.orchestrator.agents.get(entry.agent)
        if agent is None:
            return self._send_json(
                req, 404,
                {"error": f"agent {entry.agent!r} not registered"},
            )
        # Synthesize a task id that ties the inverse back to the
        # original forward task in the audit + bus logs.
        task_id = f"rollback:{rollback_id}"
        try:
            gated = gate_tools(agent, self.orchestrator.ctx, task_id)
        except Exception as exc:
            logger.exception("gate_tools failed for rollback %s", rollback_id)
            return self._send_json(
                req, 500, {"error": f"{type(exc).__name__}: {exc}"}
            )
        target = next((t for t in gated if t.name == entry.inverse_tool), None)
        if target is None:
            return self._send_json(
                req, 404,
                {"error": f"inverse tool {entry.inverse_tool!r} not on agent {entry.agent!r}"},
            )
        # Surface the rollback on the bus so the live feed shows it.
        try:
            from agentlib import new_message
            self.bus.publish(new_message(
                task_id=task_id, sender="human", recipient=entry.agent,
                kind="task",
                payload={
                    "natural_language": f"[rollback] {entry.description}",
                    "inputs": entry.inverse_args,
                },
            ))
        except Exception:
            pass
        try:
            result = target.invoke(entry.inverse_args)
        except Exception as exc:
            logger.exception("rollback invoke failed: %s", rollback_id)
            return self._send_json(req, 500, {
                "task_id": task_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
        result_str = str(result)[:500]
        try:
            store.mark_executed(rollback_id, result=result_str)
        except Exception as exc:
            logger.warning("rollback mark_executed failed: %s", exc)
        try:
            from agentlib import new_message
            self.bus.publish(new_message(
                task_id=task_id, sender=entry.agent, recipient="human",
                kind="result",
                payload={"status": "success", "summary": result_str},
            ))
        except Exception:
            pass
        return self._send_json(req, 200, {
            "rollback_id": rollback_id,
            "task_id": task_id,
            "agent": entry.agent,
            "tool": entry.inverse_tool,
            "result": result if isinstance(
                result, (str, int, float, bool, type(None), list, dict)
            ) else str(result),
        })

    @staticmethod
    def _rollback_to_dict(entry: Any) -> dict:
        return {
            "rollback_id": entry.rollback_id,
            "task_id": entry.task_id,
            "agent": entry.agent,
            "forward_tool": entry.forward_tool,
            "forward_args": entry.forward_args,
            "inverse_tool": entry.inverse_tool,
            "inverse_args": entry.inverse_args,
            "description": entry.description,
            "snapshot": entry.snapshot,
            "ts": entry.ts,
            "executed": entry.executed,
            "executed_ts": entry.executed_ts,
            "executed_result": entry.executed_result,
        }

    def _handle_memory_feedback(
        self, req: BaseHTTPRequestHandler, task_id: str
    ) -> None:
        """Attach user feedback to a memory entry.

        POST body: ``{"feedback": "good" | "bad" | null,
                      "correction": "..." | null}``
        Both fields are optional but at least one must be present —
        a POST with neither is a 400."""
        memory = getattr(self.orchestrator, "memory", None)
        if memory is None or not hasattr(memory, "annotate"):
            return self._send_json(
                req, 409, {"error": "memory store does not support feedback"}
            )

        length = int(req.headers.get("Content-Length") or 0)
        try:
            raw = req.rfile.read(length).decode("utf-8") if length else "{}"
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON body"})
        if not isinstance(body, dict):
            return self._send_json(
                req, 400, {"error": "body must be a JSON object"}
            )

        feedback = body.get("feedback")
        correction = body.get("correction")
        if feedback is None and correction is None:
            return self._send_json(
                req, 400, {"error": "supply at least one of feedback / correction"}
            )
        if feedback is not None and feedback not in ("good", "bad"):
            return self._send_json(
                req, 400,
                {"error": "feedback must be 'good', 'bad', or null"},
            )

        try:
            updated = memory.annotate(
                task_id=task_id, feedback=feedback, correction=correction
            )
        except ValueError as exc:
            return self._send_json(req, 400, {"error": str(exc)})
        except Exception as exc:
            logger.warning("memory annotate failed: %s", exc)
            return self._send_json(req, 500, {"error": "annotate failed"})

        if not updated:
            return self._send_json(
                req, 404, {"error": f"no memory entry for task_id {task_id!r}"}
            )
        return self._send_json(req, 200, {"updated": True, "task_id": task_id})

    def _handle_telemetry(self, req: BaseHTTPRequestHandler) -> None:
        """Aggregate task-record cost into a single response.

        Body shape:
          {
            "totals": {"tasks": N, "settled": M, "usd": F, "input_tokens": I,
                       "output_tokens": O, "wall_seconds": W},
            "by_agent": {"sysadmin": {tasks, usd, input_tokens, output_tokens, wall_seconds}, ...},
            "by_status": {"success": N, "failed": M, "rejected": K, ...},
            "recent": [<TaskRecord-as-dict>, ...]   # last 10
          }

        "settled" means status != pending/running. The aggregate ignores
        in-flight tasks so an unfinished run can't pull the averages
        toward zero."""
        with self._tasks_lock:
            tasks = list(self._tasks.values())
        settled_statuses = {"success", "failed", "rejected", "cancelled"}
        settled = [t for t in tasks if t.status in settled_statuses]

        def _add(into: dict, t: TaskRecord) -> None:
            into["tasks"] = into.get("tasks", 0) + 1
            into["usd"] = into.get("usd", 0.0) + (t.cost_usd or 0.0)
            into["input_tokens"] = into.get("input_tokens", 0) + (t.input_tokens or 0)
            into["output_tokens"] = into.get("output_tokens", 0) + (t.output_tokens or 0)
            into["wall_seconds"] = into.get("wall_seconds", 0.0) + (t.wall_seconds or 0.0)

        totals: dict = {
            "tasks": len(tasks),
            "settled": len(settled),
            "usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "wall_seconds": 0.0,
        }
        by_agent: dict[str, dict] = {}
        by_status: dict[str, int] = {}
        for t in tasks:
            by_status[t.status] = by_status.get(t.status, 0) + 1
        for t in settled:
            totals["usd"] += t.cost_usd or 0.0
            totals["input_tokens"] += t.input_tokens or 0
            totals["output_tokens"] += t.output_tokens or 0
            totals["wall_seconds"] += t.wall_seconds or 0.0
            agent_key = t.agent or "unknown"
            agent_bucket = by_agent.setdefault(agent_key, {})
            _add(agent_bucket, t)

        recent = sorted(tasks, key=lambda t: t.submitted_at, reverse=True)[:10]
        recent_payload = [
            {
                "task_id": t.task_id,
                "agent": t.agent,
                "status": t.status,
                "submitted_at": t.submitted_at,
                "cost_usd": t.cost_usd,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
                "wall_seconds": t.wall_seconds,
                "natural_language": t.natural_language[:120],
            }
            for t in recent
        ]

        self._send_json(req, 200, {
            "totals": totals,
            "by_agent": by_agent,
            "by_status": by_status,
            "recent": recent_payload,
        })

    def _handle_list_mcp_servers(self, req: BaseHTTPRequestHandler) -> None:
        """List MCP servers wired into the dashboard at startup.

        Returns ``{"servers": [<server-record>, ...]}`` where each
        record carries the human-relevant fields (name, target agent,
        tool count + names, destructive set, command summary) but NOT
        the raw client/transport (no need to leak that to the UI)."""
        servers = [
            {
                "name": s["name"],
                "target_agent": s.get("target_agent"),
                "command": s.get("command_summary"),
                "tool_count": len(s.get("tools", [])),
                "tools": [t["name"] for t in s.get("tools", [])],
                "destructive": sorted(s.get("destructive", [])),
                "status": s.get("status", "connected"),
                "error": s.get("error"),
            }
            for s in self.mcp_servers
        ]
        self._send_json(req, 200, {"servers": servers})

    def _handle_list_mcp_tools(
        self, req: BaseHTTPRequestHandler, server_name: str
    ) -> None:
        """Catalog of one MCP server's tools, with the full descriptor
        each (description, args schema). UI uses this to render an
        inspectable tool list under each server card."""
        for s in self.mcp_servers:
            if s["name"] == server_name:
                return self._send_json(req, 200, {
                    "name": s["name"],
                    "tools": s.get("tools", []),
                })
        return self._send_json(
            req, 404, {"error": f"unknown MCP server {server_name!r}"}
        )

    def _handle_list_memory(self, req: BaseHTTPRequestHandler) -> None:
        """List recent memory entries.

        Supports a query string: ``?q=<text>&k=<int>&agent=<name>``.
        When ``q`` is provided, returns top-K most-similar entries via
        the orchestrator's memory store. Otherwise returns the most
        recent entries (still bounded by ``k``, default 25)."""
        from urllib.parse import parse_qs, urlparse

        memory = getattr(self.orchestrator, "memory", None)
        if memory is None:
            return self._send_json(req, 200, {"entries": []})

        query_string = urlparse(req.path).query
        params = parse_qs(query_string)
        q = (params.get("q") or [""])[0]
        try:
            k = max(1, min(int((params.get("k") or ["25"])[0]), 100))
        except ValueError:
            k = 25
        agent = (params.get("agent") or [None])[0]

        try:
            if q:
                entries = memory.search(q, k=k, agent=agent)
            else:
                # No query → most recent. Stores expose .search but no
                # .all() — peek at well-known internals (set on every
                # built-in store) to enumerate, then apply agent +
                # k bounds in Python.
                if hasattr(memory, "_load_entries_raw"):
                    raw_entries, _ = memory._load_entries_raw()
                    all_entries = list(reversed(raw_entries))
                elif hasattr(memory, "_entries"):
                    all_entries = list(reversed(memory._entries))
                else:
                    # Unknown store shape — fall back to a generic
                    # lexical query that lets the agent filter still work.
                    all_entries = memory.search("task", k=k, agent=agent)
                if agent is not None:
                    all_entries = [e for e in all_entries if e.agent == agent]
                entries = all_entries[:k]
        except Exception as exc:
            logger.warning("memory list failed: %s", exc)
            entries = []

        payload = [
            {
                "task_id": e.task_id,
                "agent": e.agent,
                "natural_language": e.natural_language,
                "summary": e.summary,
                "status": e.status,
                "ts": e.ts,
                "metadata": e.metadata,
            }
            for e in entries
        ]
        self._send_json(req, 200, {"entries": payload})

    def _handle_list_terraform_stacks(self, req: BaseHTTPRequestHandler) -> None:
        """Scan infra/terraform/ for directories that look like a stack
        (contain at least one .tf file). Returns relative paths."""
        roots = self._infra_roots("terraform")
        stacks: list[str] = []
        for root in roots:
            for tf_dir in sorted({p.parent for p in root.rglob("*.tf")}):
                stacks.append(str(tf_dir.relative_to(root.parent)))
        self._send_json(req, 200, sorted(set(stacks)))

    def _handle_list_ansible_playbooks(self, req: BaseHTTPRequestHandler) -> None:
        """Scan infra/ansible/ for top-level *.yml playbooks."""
        roots = self._infra_roots("ansible")
        plays: list[str] = []
        for root in roots:
            for yml in sorted(root.glob("*.yml")):
                plays.append(str(yml.relative_to(root.parent)))
        self._send_json(req, 200, sorted(set(plays)))

    def _infra_roots(self, kind: str) -> list[Path]:
        """Resolve infra/<kind> against a few likely repo locations.

        Container layout has it at /opt/olympus/infra/<kind>;
        dev-box layout has it at the project root walked up from this
        file. We try both and return only existing paths."""
        candidates = [
            Path("/opt/olympus/infra") / kind,
            Path(__file__).resolve().parent.parent.parent.parent.parent / "infra" / kind,
        ]
        return [p for p in candidates if p.is_dir()]

    # ---- SSE streaming ----

    def _handle_task_events(
        self, req: BaseHTTPRequestHandler, task_id: str
    ) -> None:
        self._serve_sse(req, task_filter=task_id)

    def _handle_all_events(self, req: BaseHTTPRequestHandler) -> None:
        self._serve_sse(req, task_filter=None)

    def _serve_sse(
        self, req: BaseHTTPRequestHandler, task_filter: Optional[str]
    ) -> None:
        req.send_response(200)
        req.send_header("Content-Type", "text/event-stream")
        req.send_header("Cache-Control", "no-cache")
        req.send_header("X-Accel-Buffering", "no")
        req.end_headers()

        # Replay history first so a client that attaches mid-task still
        # sees what already happened. Then live-tail via a subscription.
        for msg in self.bus.log:
            if task_filter and msg.task_id != task_filter:
                continue
            if not _send_sse_event(req, msg):
                return  # client disconnected

        live: list[BusMessage] = []
        cond = threading.Condition()

        def sink(m: BusMessage) -> None:
            if task_filter and m.task_id != task_filter:
                return
            with cond:
                live.append(m)
                cond.notify()

        self.bus.subscribe("*", sink)

        # Heartbeat every 15s so the connection survives intermediaries.
        last_heartbeat = time.monotonic()
        while True:
            with cond:
                cond.wait(timeout=1.0)
                pending = list(live)
                live.clear()
            for m in pending:
                if not _send_sse_event(req, m):
                    return
            now = time.monotonic()
            if now - last_heartbeat > 15.0:
                try:
                    req.wfile.write(b": heartbeat\n\n")
                    req.wfile.flush()
                    last_heartbeat = now
                except (ConnectionError, BrokenPipeError):
                    return


def _send_sse_event(req: BaseHTTPRequestHandler, msg: BusMessage) -> bool:
    payload = {
        "msg_id": msg.msg_id,
        "task_id": msg.task_id,
        "sender": msg.sender,
        "recipient": msg.recipient,
        "kind": msg.kind,
        "timestamp": msg.timestamp,
        "payload": _payload_to_jsonable(msg.payload),
        "causation_id": msg.causation_id,
    }
    line = f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
    try:
        req.wfile.write(line)
        req.wfile.flush()
        return True
    except (ConnectionError, BrokenPipeError):
        return False


def _as_base_tool(raw: Any) -> BaseTool:
    """Tools on AgentSpec.tools may be either @tool-decorated functions
    (which expose .name / .description / .args_schema) or raw callables.
    For the UI catalog we just want a duck-typed BaseTool view."""
    if isinstance(raw, BaseTool):
        return raw
    # Last-resort: synthesize a minimal stand-in. We never invoke through
    # this path — gate_tools handles the real wrapping — but the catalog
    # endpoint should not crash on an unusual entry.
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(raw)


def _tool_args_schema(tool: BaseTool) -> dict:
    """Return the JSON schema for a tool's args (for UI form generation).
    Tolerates schema being a dict, a Pydantic class, or absent entirely."""
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return {"type": "object", "properties": {}}
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    return {"type": "object", "properties": {}}


def _payload_to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _payload_to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_payload_to_jsonable(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _payload_to_jsonable(dataclasses.asdict(value))
    return repr(value)


# ---- helper to wire a default DashboardServer ----


def build_default_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    router: Optional[Router] = None,
    audit_log_path: str = DEFAULT_AUDIT_LOG,
    memory: Optional[MemoryStore] = None,
    memory_log_path: Optional[str] = None,
    rollback: Optional[RollbackStore] = None,
    rollback_log_path: Optional[str] = None,
    mcp_servers: Optional[list[dict[str, Any]]] = None,
) -> DashboardServer:
    """Construct a DashboardServer with the four production agents and
    an in-memory bus. Convenience for the CLI entry point.

    Memory backend resolution:
      - ``memory=`` wins when provided.
      - Else, if ``OLYMPUS_MEMORY=disabled`` → no memory.
      - Else, if ``OLYMPUS_MEMORY=embeddings`` →
        ``EmbeddingMemoryStore`` next to the audit log.
      - Else, ``JsonlMemoryStore`` at ``memory_log_path`` (defaults to
        a sibling of ``audit_log_path``).

    MCP servers: each dict in ``mcp_servers`` describes one server to
    wire onto an agent at startup. Shape:
      {
        "name": "filesystem",
        "target_agent": "programmer",
        "config": MCPServerConfig(...),
        "client": MCPClient(...) | None,   # if None, StdioTransport is built
      }
    Failures during registration are recorded on the registry entry
    (status="error") rather than crashing the dashboard — a flaky
    third-party server shouldn't take Olympus offline.
    """
    from olympus_cli.registry import build_orchestrator, default_agents

    bus = InMemoryBus()
    approval_hook = QueueApprovalHook()
    if rollback is None and os.environ.get("OLYMPUS_ROLLBACK", "").lower() != "disabled":
        rollback_log_path = rollback_log_path or str(
            Path(audit_log_path).with_name("rollback.jsonl")
        )
        rollback = JsonlRollbackStore(rollback_log_path)
    ctx = AgentContext(
        approval=approval_hook,
        audit=JsonlAuditLogger(audit_log_path),
        rollback=rollback,
    )
    if memory is None:
        mode = os.environ.get("OLYMPUS_MEMORY", "").lower()
        if mode != "disabled":
            memory_log_path = memory_log_path or str(
                Path(audit_log_path).with_name("memory.jsonl")
            )
            if mode == "embeddings":
                memory = EmbeddingMemoryStore(
                    memory_log_path.replace(".jsonl", ".emb.jsonl")
                )
            else:
                memory = JsonlMemoryStore(memory_log_path)
    agents = default_agents()
    mcp_registry = _wire_mcp_servers(agents, mcp_servers or [])
    orch = build_orchestrator(
        ctx=ctx,
        agents=agents,
        router=router,
        bus=bus,
        memory=memory,
    )
    return DashboardServer(
        orchestrator=orch,
        bus=bus,
        approval_hook=approval_hook,
        audit_log_path=audit_log_path,
        host=host,
        port=port,
        mcp_servers=mcp_registry,
    )


def _wire_mcp_servers(
    agents: list[Any], configs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Register each MCP server's tools onto its target agent and
    return a registry dicts list for ``DashboardServer.mcp_servers``.

    Each input dict: ``{name, target_agent, config, client?}``.
    ``config`` is an ``MCPServerConfig`` instance. If ``client`` is
    None, the right transport is chosen by ``build_transport``
    (HTTP if ``config.url`` is set, stdio otherwise); pass an
    explicit client in tests to use ``MockTransport``.

    Failures are isolated: a misbehaving server lands as
    ``status="error"`` on the registry; other servers still register."""
    from agentlib import MCPClient, build_transport, register_mcp_tools

    by_name = {a.name: a for a in agents}
    registry: list[dict[str, Any]] = []
    for entry in configs:
        name = entry["name"]
        target = entry.get("target_agent")
        config = entry["config"]
        client = entry.get("client")
        # HTTP-transport configs surface their url; stdio configs
        # surface the subprocess command. The UI uses this for the
        # "$ ..." line under each server card.
        if getattr(config, "url", ""):
            summary = f"HTTP {config.url}"
        elif getattr(config, "command", ""):
            summary = f"{config.command} {' '.join(config.args)}".strip()
        else:
            summary = ""
        record: dict[str, Any] = {
            "name": name,
            "target_agent": target,
            "command_summary": summary,
            "destructive": set(getattr(config, "destructive", set())),
            "status": "connected",
            "tools": [],
            "error": None,
        }
        if target not in by_name:
            record["status"] = "error"
            record["error"] = f"unknown target_agent {target!r}"
            registry.append(record)
            continue
        try:
            if client is None:
                client = MCPClient(build_transport(config))
                client.initialize()
            tools = client.list_tools()
            register_mcp_tools(by_name[target], config, client=client)
            record["tools"] = tools
        except Exception as exc:
            logger.warning("MCP server %r registration failed: %s", name, exc)
            record["status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
        registry.append(record)
    return registry
