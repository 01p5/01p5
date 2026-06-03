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

Group-chat tickets (the main agent + dispatched sub-agents in one thread):

- ``POST /tickets/{id}/messages`` — body: ``{message}``. Posts a human turn
                                    and runs the main agent on the ticket.
- ``POST /tickets/{id}/close``    — summarize the ticket to memory + discard
                                    its per-agent checkpoints.
- ``GET /tickets``                — list past sessions (id, preview, counts).
- ``GET /tickets/{id}``           — the ticket's full transcript.
- ``GET /tickets/{id}/events``    — SSE stream of the ticket transcript
                                    (human + agent messages, dispatches,
                                    tool calls, ask_agent exchanges).

Auth (Phase A — Google OAuth + session cookie):

- ``GET /me``                     — `{authenticated, email}` for the SPA's
                                    RequireAuth (401 if no/invalid session).
- ``GET /auth/google/start``      — 302 → Google consent screen.
- ``GET /auth/google/callback``   — 302 → ``/`` on success, sets session cookie.
- ``POST /auth/email/start``      — body ``{email}``: send a one-time code
                                    (allowlisted domains only, rate-limited).
- ``POST /auth/email/verify``     — body ``{email, code}``: mint a session.
- ``POST /auth/logout``           — clears the session cookie.

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
    AlwaysReject,
    BudgetGuard,
    Bus,
    BusMessage,
    EmbeddingMemoryStore,
    FileBackedInventoryStore,
    InMemoryBus,
    InMemoryInventoryStore,
    InMemoryTicketStore,
    InventoryError,
    InventoryStore,
    JsonlAuditLogger,
    JsonlMemoryStore,
    JsonlRollbackStore,
    MemoryStore,
    SelfProtectionPolicy,
    Orchestrator,
    QueueApprovalHook,
    RollbackStore,
    Router,
    TaskMessage,
    TicketEvent,
    TicketStore,
    gate_tools,
    get_cost_for_type,
    render_ansible_inventory,
)
from langchain_core.tools import BaseTool

from .auth import (
    OAUTH_STATE_COOKIE,
    AuthConfig,
    Authenticator,
    GoogleAuthError,
    RateLimitedError,
    clear_cookie_header,
    exchange_code_for_email,
    google_authorize_url,
    new_oauth_state,
    parse_cookie_header,
    public_status,
    set_cookie_header,
)
from .accounting import SqliteAccountingStore
from .proxy import proxy_request
from .terminal import SessionManager, session_to_info
from .user_limits import (
    FileBackedUserLimitStore,
    InMemoryUserLimitStore,
    UserLimitStore,
)
from .terminal_ws import WSHandshakeError, bridge_session_to_socket, perform_ws_handshake

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
    # ADM.2 — submitting user's email (None for bypass/system tasks).
    # Drives the per-user accounting rollup on the admin dashboard.
    owner_email: Optional[str] = None


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
        ticket_store: Optional[TicketStore] = None,
        auth: Optional[Authenticator] = None,
        inventory_store: Optional[InventoryStore] = None,
        session_manager: Optional["SessionManager"] = None,
        user_limit_store: Optional["UserLimitStore"] = None,
        accounting_store: Optional[SqliteAccountingStore] = None,
        default_user_daily_limit_usd: Optional[float] = None,
    ):
        self.orchestrator = orchestrator
        self.bus = bus
        self.approval_hook = approval_hook
        self.audit_log_path = audit_log_path
        # Auth (Phase A). When no Authenticator is passed, default to a
        # bypass-on config — the existing test fixtures construct the
        # server without auth and expect open access; production
        # build_default_server wires a real Authenticator from env.
        self.auth: Authenticator = auth or Authenticator(AuthConfig(bypass=True))
        # User-managed hosts + ssh keys. Default is in-memory (tests
        # construct DashboardServer without one); build_default_server
        # swaps in a FileBackedInventoryStore against the persistent volume.
        self.inventory_store: InventoryStore = inventory_store or InMemoryInventoryStore()
        # ADM.3 — per-user daily cost caps, set via the admin dashboard.
        # In-memory default (tests + ephemeral deploys); build_default_server
        # swaps in a FileBackedUserLimitStore on the persistent volume.
        self.user_limit_store: UserLimitStore = user_limit_store or InMemoryUserLimitStore()
        # ACCT-SQL.1 — durable per-task cost ledger. Default :memory:
        # (ephemeral; tests + dev). build_default_server points it at
        # accounting.db on the audit volume.
        self.accounting: SqliteAccountingStore = accounting_store or SqliteAccountingStore(":memory:")
        # Per-user daily cap that applies when an admin hasn't set an
        # explicit one. None = no default (unlimited unless set). The
        # demo sets this to $10 so every user is capped out of the box.
        self.default_user_daily_limit_usd: Optional[float] = default_user_daily_limit_usd
        # TERM.2a — operator-driven SSH sessions hosted in this pod.
        # Pool of PTY-wrapped ssh subprocesses, keyed by session_id +
        # owner_email. The REST endpoints below + the WS bridge (TERM.2b)
        # are thin transports over this. Default fresh SessionManager
        # if the caller didn't pre-build one.
        self.session_manager: SessionManager = session_manager or SessionManager()
        # Group-chat ticket transcript (Phase 2). The orchestrator writes
        # dispatch/result/tool_call/ask events here; the dashboard records
        # human + main-agent messages and streams the whole transcript per
        # ticket over SSE. None => group chat disabled (endpoints 404).
        self.ticket_store = ticket_store
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
        # Per-agent cost telemetry — fed by the orchestrator's cost_sink once
        # per agent run (main + dispatched specialists), so /telemetry's
        # by_agent reflects sub-agent spend. Session-scoped (in-memory), like
        # self._tasks; the durable per-user ledger lives in accounting.db.
        self._agent_telemetry: dict[str, dict] = {}
        # Ephemeral per-ticket token-stream subscribers (GET /tickets/{id}/stream).
        # The main agent's token_sink pushes reply chunks here; NOT persisted —
        # the authoritative reply still lands once as an agent_message on the
        # transcript. ticket_id -> list[queue.Queue].
        self._stream_subs: dict[str, list] = {}
        self._stream_lock = threading.Lock()
        self._mcp_lock = threading.Lock()

        # Subscribe an internal sink to mark tasks as completed when the
        # orchestrator's "result" message lands on the bus.
        self.bus.subscribe("orchestrator", self._on_orchestrator_msg)

        self._server: Optional[ThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._host = host
        self._port = port

        # S2.C2 — reverse-proxy targets for the sibling slurm-dashboard
        # + gpu-dashboard pods (Stage 2 of the HPC integration). Path
        # prefix → upstream base URL. Empty when unset means the prefix
        # isn't a proxy route (do_GET/POST etc. will fall through to
        # normal routing → 404 → SPA fallback). Env-driven so the chart
        # values feed it without code changes.
        self.proxy_targets: dict[str, str] = {}
        for prefix, env_var in (("/slurm", "OLYMPUS_PROXY_SLURM_URL"),
                                 ("/gpu",   "OLYMPUS_PROXY_GPU_URL")):
            url = os.environ.get(env_var, "").strip()
            if url:
                self.proxy_targets[prefix] = url

        # Filter the router's catalog against whichever MCP servers
        # were wired at construction time (build_default_server
        # mcp_servers=). Conditional agents like HPC stay out of
        # candidate lists until their prerequisites are met.
        self._refresh_active_agents()

    def _refresh_active_agents(self) -> None:
        """Refresh the orchestrator's router catalog with the current
        set of connected MCP server names. Called from __init__ and
        from every MCP add/remove path."""
        if self.orchestrator is None:
            return
        connected = {s.get("name") for s in self.mcp_servers if s.get("name")}
        try:
            self.orchestrator.refresh_active_agents(connected)
        except AttributeError:
            # Older orchestrator without the method — silently no-op
            # so the dashboard can keep running.
            pass

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
        # Mirror the settled cost into the durable ledger (outside the
        # tasks lock — the accounting store has its own).
        self.accounting.record_result(
            msg.task_id, status=rec.status, cost_usd=rec.cost_usd,
            input_tokens=rec.input_tokens, output_tokens=rec.output_tokens,
            wall_seconds=rec.wall_seconds, agent=rec.agent,
        )

    def _record_agent_cost(self, agent: str, cost: Any) -> None:
        """Per-agent telemetry sink (wired to the orchestrator's cost_sink).
        Accumulates one bucket per agent across the session so the footer
        shows main + each specialist that ran, not just the coordinator."""
        if cost is None:
            return
        with self._tasks_lock:
            b = self._agent_telemetry.setdefault(
                agent or "unknown",
                {"tasks": 0, "usd": 0.0, "input_tokens": 0, "output_tokens": 0, "wall_seconds": 0.0},
            )
            b["tasks"] += 1
            b["usd"] += getattr(cost, "total_usd", 0.0) or 0.0
            b["input_tokens"] += getattr(cost, "input_tokens", 0) or 0
            b["output_tokens"] += getattr(cost, "output_tokens", 0) or 0
            b["wall_seconds"] += getattr(cost, "wall_seconds", 0.0) or 0.0

    # ---- task submission (worker thread) ----

    def submit(self, natural_language: str, owner_email: Optional[str] = None) -> str:
        task_id = str(uuid.uuid4())
        rec = TaskRecord(
            task_id=task_id,
            natural_language=natural_language,
            submitted_at=time.time(),
            owner_email=owner_email,
        )
        with self._tasks_lock:
            self._tasks[task_id] = rec
        self.accounting.record_submitted(
            task_id, owner_email=owner_email, natural_language=natural_language,
            submitted_at=rec.submitted_at, status="pending",
        )

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

    # ---- group-chat ticket submission (worker thread) ----

    def submit_ticket(self, ticket_id: str, message: str, owner_email: Optional[str] = None) -> None:
        """Post a human message into a ticket and run the main agent on it.

        The human turn is recorded synchronously (so the SSE stream shows it
        immediately); the main agent runs in a worker thread, dispatching
        specialists as it sees fit. The main agent's reply is recorded as an
        ``agent_message`` event. Specialist dispatches, tool calls, and
        ask_agent exchanges land on the transcript via the orchestrator."""
        if self.ticket_store is None:
            raise RuntimeError("group chat disabled: no ticket store wired")

        self.ticket_store.append(
            TicketEvent(
                ticket_id=ticket_id,
                actor="human",
                kind="human_message",
                payload={"text": message},
                task_id=ticket_id,
            )
        )

        # ADM.2 — record a TaskRecord for this chat turn so it shows up in
        # telemetry + the admin per-user accounting, attributed to the
        # submitting user. The chat path uses dispatch_to (synchronous,
        # returns the result directly) rather than the bus "result" message
        # that populates /tasks records — so we populate the record here
        # from result.cost instead of relying on _on_orchestrator_msg.
        inner_task_id = str(uuid.uuid4())
        rec = TaskRecord(
            task_id=inner_task_id,
            natural_language=message,
            submitted_at=time.time(),
            status="running",
            agent="main",
            owner_email=owner_email,
        )
        with self._tasks_lock:
            self._tasks[inner_task_id] = rec
        self.accounting.record_submitted(
            inner_task_id, owner_email=owner_email, natural_language=message,
            submitted_at=rec.submitted_at, agent="main", status="running",
        )

        # Reset any prior Stop so this new turn isn't dead-on-arrival.
        try:
            self.orchestrator.clear_cancel(ticket_id)
        except AttributeError:
            pass  # older orchestrator without cancellation

        def worker():
            task = TaskMessage(
                task_id=inner_task_id,
                natural_language=message,
                ticket_id=ticket_id,
                parent_task_id=ticket_id,
            )
            try:
                # announce=False: the main agent's own turn is recorded as a
                # single agent_message below, not as a dispatch+result pair.
                # with_memory: pull context from prior closed tickets.
                # aggregate_cost: the recorded per-user cost must include the
                # specialists the main agent dispatched, not just the
                # coordinator's own tokens (see _sum_costs in the orchestrator).
                result = self.orchestrator.dispatch_to(
                    "main", task, announce=False, with_memory=True,
                    aggregate_cost=True,
                )
                payload = {
                    "text": result.summary,
                    "status": result.status,
                    "resolved": bool((result.artifacts or {}).get("resolved")),
                }
                with self._tasks_lock:
                    rec.status = result.status or "success"
                    rec.result_summary = result.summary
                    cost = getattr(result, "cost", None)
                    if cost is not None:
                        rec.cost_usd = getattr(cost, "total_usd", None)
                        rec.input_tokens = getattr(cost, "input_tokens", None)
                        rec.output_tokens = getattr(cost, "output_tokens", None)
                        rec.wall_seconds = getattr(cost, "wall_seconds", None)
                self.accounting.record_result(
                    inner_task_id, status=rec.status, cost_usd=rec.cost_usd,
                    input_tokens=rec.input_tokens, output_tokens=rec.output_tokens,
                    wall_seconds=rec.wall_seconds, agent="main",
                )
            except Exception as exc:
                logger.exception("ticket %s main-agent turn failed", ticket_id)
                with self._tasks_lock:
                    rec.status = "failed"
                    rec.error = f"{type(exc).__name__}: {exc}"
                self.accounting.record_result(
                    inner_task_id, status="failed", cost_usd=rec.cost_usd,
                    input_tokens=rec.input_tokens, output_tokens=rec.output_tokens,
                    wall_seconds=rec.wall_seconds, agent="main",
                )
                payload = {
                    "text": f"main agent error: {type(exc).__name__}: {exc}",
                    "status": "failed",
                    "resolved": False,
                }
            self.ticket_store.append(
                TicketEvent(
                    ticket_id=ticket_id,
                    actor="main",
                    kind="agent_message",
                    payload=payload,
                    task_id=ticket_id,
                )
            )

        threading.Thread(
            target=worker, name=f"dashboard-ticket:{ticket_id}", daemon=True
        ).start()

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
        # TERM.2a — reap every live PTY + ssh subprocess so the
        # dashboard pod doesn't leave orphaned children behind.
        try:
            self.session_manager.shutdown_all()
        except Exception:
            logger.warning("session_manager.shutdown_all raised", exc_info=True)

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
                # Auth gate (Phase A): API/SSE/data prefixes require a session.
                # SPA shell + /me + /auth/* + /healthz stay public so the
                # unauthenticated SPA can load its own /login route.
                if outer.auth.requires_auth_get(path) and outer.auth.session_from_request(self.headers) is None:
                    return outer._send_json(self, 401, {"error": "unauthenticated"})
                # S2.C2 — reverse-proxy /slurm/* and /gpu/* to sibling
                # dashboards (only when configured via OLYMPUS_PROXY_*
                # env vars). Auth gate above ran first, so the proxied
                # surfaces inherit Olympus's session cookie.
                for _pfx, _t in outer.proxy_targets.items():
                    if path == _pfx or path.startswith(_pfx + "/"):
                        return proxy_request(self, _t, _pfx)
                if path == "/" or path == "/index.html":
                    return outer._serve_static(self, "index.html")
                if path == "/healthz":
                    return outer._send_json(self, 200, {"ok": True})
                if path == "/me":
                    return outer._handle_me(self)
                if path == "/auth/google/start":
                    return outer._handle_auth_google_start(self)
                if path == "/auth/google/callback":
                    return outer._handle_auth_google_callback(self)
                if path == "/tasks":
                    return outer._handle_list_tasks(self)
                if path.startswith("/tasks/"):
                    rest = path[len("/tasks/"):]
                    if rest.endswith("/events"):
                        return outer._handle_task_events(self, rest[: -len("/events")])
                    return outer._handle_get_task(self, rest)
                if path == "/events":
                    return outer._handle_all_events(self)
                if path == "/tickets":
                    return outer._handle_list_tickets_group(self)
                if path.startswith("/tickets/"):
                    rest = path[len("/tickets/"):]
                    if rest.endswith("/events"):
                        return outer._handle_ticket_events(self, rest[: -len("/events")])
                    if rest.endswith("/stream"):
                        return outer._serve_token_stream(self, rest[: -len("/stream")])
                    return outer._handle_get_ticket(self, rest)
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
                if path == "/admin/accounting":
                    return outer._handle_admin_accounting(self)
                if path == "/admin/activity":
                    return outer._handle_admin_activity(self)
                if path == "/mcp/servers":
                    return outer._handle_list_mcp_servers(self)
                if path.startswith("/mcp/servers/") and path.endswith("/tools"):
                    inner = path[len("/mcp/servers/"):-len("/tools")]
                    return outer._handle_list_mcp_tools(self, inner)
                if path == "/stacks/terraform":
                    return outer._handle_list_terraform_stacks(self)
                if path == "/stacks/ansible":
                    return outer._handle_list_ansible_playbooks(self)
                if path == "/inventory/hosts":
                    return outer._handle_list_hosts(self)
                if path == "/inventory/keys":
                    return outer._handle_list_keys(self)
                if path == "/inventory/render":
                    return outer._handle_render_inventory(self)
                if path == "/terminal/sessions":
                    return outer._handle_list_terminal_sessions(self)
                if path.startswith("/terminal/sessions/") and path.endswith("/ws"):
                    sid = path[len("/terminal/sessions/"):-len("/ws")]
                    return outer._handle_terminal_ws(self, sid)
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
                path = self.path.partition("?")[0]
                if outer.auth.requires_auth_post(path) and outer.auth.session_from_request(self.headers) is None:
                    return outer._send_json(self, 401, {"error": "unauthenticated"})
                for _pfx, _t in outer.proxy_targets.items():
                    if path == _pfx or path.startswith(_pfx + "/"):
                        return proxy_request(self, _t, _pfx)
                if path == "/auth/logout":
                    return outer._handle_auth_logout(self)
                if path == "/auth/email/start":
                    return outer._handle_email_start(self)
                if path == "/auth/email/verify":
                    return outer._handle_email_verify(self)
                if self.path == "/tasks":
                    return outer._handle_post_task(self)
                if self.path.startswith("/tickets/") and self.path.endswith("/messages"):
                    inner = self.path[len("/tickets/"):-len("/messages")]
                    return outer._handle_post_ticket_message(self, inner)
                if self.path.startswith("/tickets/") and self.path.endswith("/close"):
                    inner = self.path[len("/tickets/"):-len("/close")]
                    return outer._handle_close_ticket(self, inner)
                if self.path.startswith("/tickets/") and self.path.endswith("/cancel"):
                    inner = self.path[len("/tickets/"):-len("/cancel")]
                    return outer._handle_cancel_ticket(self, inner)
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
                if self.path == "/mcp/servers":
                    return outer._handle_add_mcp_server(self)
                if self.path.startswith("/tools/"):
                    rest = self.path[len("/tools/"):]
                    if "/" in rest:
                        agent_name, _, tool_name = rest.partition("/")
                        return outer._handle_invoke_tool(self, agent_name, tool_name)
                if self.path == "/inventory/hosts":
                    return outer._handle_post_host(self)
                if self.path == "/inventory/keys":
                    return outer._handle_post_key(self)
                if self.path == "/inventory/sync-terraform":
                    return outer._handle_sync_terraform_inventory(self)
                if self.path == "/terminal/sessions":
                    return outer._handle_post_terminal_session(self)
                if self.path.startswith("/terminal/sessions/") and self.path.endswith("/ask"):
                    sid = self.path[len("/terminal/sessions/"):-len("/ask")]
                    return outer._handle_ask_terminal_session(self, sid)
                self.send_response(404)
                self.end_headers()

            def do_PUT(self):  # noqa: N802
                path = self.path.partition("?")[0]
                if outer.auth.requires_auth_put(path) and outer.auth.session_from_request(self.headers) is None:
                    return outer._send_json(self, 401, {"error": "unauthenticated"})
                for _pfx, _t in outer.proxy_targets.items():
                    if path == _pfx or path.startswith(_pfx + "/"):
                        return proxy_request(self, _t, _pfx)
                if path.startswith("/inventory/hosts/"):
                    host_id = path[len("/inventory/hosts/"):]
                    return outer._handle_put_host(self, host_id)
                if path.startswith("/admin/limits/"):
                    from urllib.parse import unquote
                    email = unquote(path[len("/admin/limits/"):])
                    return outer._handle_admin_set_limit(self, email)
                self.send_response(404)
                self.end_headers()

            def do_DELETE(self):  # noqa: N802
                path = self.path.partition("?")[0]
                if outer.auth.requires_auth_delete(path) and outer.auth.session_from_request(self.headers) is None:
                    return outer._send_json(self, 401, {"error": "unauthenticated"})
                for _pfx, _t in outer.proxy_targets.items():
                    if path == _pfx or path.startswith(_pfx + "/"):
                        return proxy_request(self, _t, _pfx)
                if path.startswith("/mcp/servers/"):
                    name = path[len("/mcp/servers/"):]
                    return outer._handle_delete_mcp_server(self, name)
                if path.startswith("/inventory/hosts/"):
                    host_id = path[len("/inventory/hosts/"):]
                    return outer._handle_delete_host(self, host_id)
                if path.startswith("/inventory/keys/"):
                    key_id = path[len("/inventory/keys/"):]
                    return outer._handle_delete_key(self, key_id)
                if path.startswith("/terminal/sessions/"):
                    sid = path[len("/terminal/sessions/"):]
                    return outer._handle_delete_terminal_session(self, sid)
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
        # ADM.3 — per-user daily cost limit. Reject before spawning the
        # worker if the submitting user is already over their cap.
        session = self.auth.session_from_request(req.headers)
        owner = session.email if session else None
        over, detail = self._user_over_limit(owner)
        if over:
            self._send_json(req, 429, {"error": "daily cost limit reached", **detail})
            return
        task_id = self.submit(nl.strip(), owner_email=owner)
        self._send_json(req, 202, {"task_id": task_id})

    # ---- auth (Phase A) ----

    def _handle_me(self, req: BaseHTTPRequestHandler) -> None:
        """Return the authenticated user — or 401 if unauthenticated.

        Always includes the public ``auth`` status so the unauthenticated
        SPA's /login page can show only the methods that are actually
        wired (Google + email). The SPA's RequireAuth polls this on mount."""
        status = public_status(self.auth.config)
        session = self.auth.session_from_request(req.headers)
        if session is None:
            return self._send_json(req, 401, {"authenticated": False, "auth": status})
        return self._send_json(req, 200, {
            "authenticated": True,
            "email": session.email,
            "is_admin": self.auth.config.is_admin(session.email),
            "auth": status,
        })

    def _handle_auth_google_start(self, req: BaseHTTPRequestHandler) -> None:
        cfg = self.auth.config
        if not cfg.google_oauth_enabled():
            return self._send_json(req, 503, {"error": "google oauth not configured"})
        state = new_oauth_state()
        url = google_authorize_url(cfg, state)
        # CSRF: store state in a short-lived httpOnly cookie; the callback
        # compares it against the ?state= query param Google echoes back.
        req.send_response(302)
        req.send_header("Location", url)
        req.send_header(
            "Set-Cookie",
            set_cookie_header(OAUTH_STATE_COOKIE, state, max_age=600, secure=cfg.cookie_secure),
        )
        req.send_header("Content-Length", "0")
        req.end_headers()

    def _handle_auth_google_callback(self, req: BaseHTTPRequestHandler) -> None:
        cfg = self.auth.config
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(req.path).query)
        if "error" in query:
            return self._send_json(req, 400, {"error": "oauth_error", "detail": query.get("error", [""])[0]})
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        expected_state = parse_cookie_header(req.headers.get("Cookie", "") or "", OAUTH_STATE_COOKIE)
        if not code or not state or not expected_state or state != expected_state:
            return self._send_json(req, 400, {"error": "invalid_state"})
        try:
            email = exchange_code_for_email(cfg, code)
        except GoogleAuthError as exc:
            return self._send_json(req, 401, {"error": "oauth_failed", "detail": str(exc)})
        if not cfg.is_email_allowed(email):
            return self._send_json(req, 403, {"error": "email_not_allowed", "email": email})
        # Record the login (creates the user in accounting + activity feed).
        self.accounting.record_login(email, method="google")
        # Mint a session cookie, clear the state cookie, redirect to /.
        req.send_response(302)
        req.send_header("Location", "/")
        req.send_header("Set-Cookie", self.auth.mint_cookie(email))
        req.send_header(
            "Set-Cookie",
            clear_cookie_header(OAUTH_STATE_COOKIE, secure=cfg.cookie_secure),
        )
        req.send_header("Content-Length", "0")
        req.end_headers()

    def _handle_auth_logout(self, req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Set-Cookie", self.auth.clear_cookie())
        body = json.dumps({"ok": True}).encode()
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)

    def _handle_email_start(self, req: BaseHTTPRequestHandler) -> None:
        """Phase B: send a one-time code to an allowlisted email."""
        cfg = self.auth.config
        if self.auth.email_sender is None:
            return self._send_json(req, 503, {"error": "email login not configured"})
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        email = (body.get("email") or "").strip()
        if not email or "@" not in email:
            return self._send_json(req, 400, {"error": "email required"})
        if not cfg.is_email_allowed(email):
            return self._send_json(req, 403, {"error": "email_not_allowed"})
        try:
            code = self.auth.otp_store.issue(email)
        except RateLimitedError as exc:
            return self._send_json(req, 429, {
                "error": "rate_limited",
                "retry_after": exc.retry_after_seconds,
            })
        try:
            self.auth.email_sender.send_otp(email, code)
        except Exception as exc:
            logger.exception("email send failed for %s", email)
            return self._send_json(req, 502, {"error": f"send_failed: {type(exc).__name__}"})
        return self._send_json(req, 202, {"sent": True, "email": email})

    def _handle_email_verify(self, req: BaseHTTPRequestHandler) -> None:
        cfg = self.auth.config
        if self.auth.email_sender is None:
            return self._send_json(req, 503, {"error": "email login not configured"})
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        email = (body.get("email") or "").strip()
        code = (body.get("code") or "").strip()
        if not email or not code:
            return self._send_json(req, 400, {"error": "email and code required"})
        if not cfg.is_email_allowed(email):
            return self._send_json(req, 403, {"error": "email_not_allowed"})
        if not self.auth.otp_store.verify(email, code):
            return self._send_json(req, 401, {"error": "invalid_or_expired_code"})
        # Record the login (creates the user in accounting + activity feed).
        self.accounting.record_login(email, method="email-otp")
        # Match → mint session cookie.
        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Set-Cookie", self.auth.mint_cookie(email))
        body_out = json.dumps({"authenticated": True, "email": email}).encode()
        req.send_header("Content-Length", str(len(body_out)))
        req.end_headers()
        req.wfile.write(body_out)

    # ---- group-chat tickets ----

    def _handle_post_ticket_message(
        self, req: BaseHTTPRequestHandler, ticket_id: str
    ) -> None:
        if self.ticket_store is None:
            self._send_json(req, 404, {"error": "group chat not enabled"})
            return
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            self._send_json(req, 400, {"error": "invalid JSON"})
            return
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            self._send_json(req, 400, {"error": "message required"})
            return
        # ADM.3 — daily cap also gates chat (the main cost driver on the
        # demo). Same enforcement as POST /tasks.
        session = self.auth.session_from_request(req.headers)
        owner = session.email if session else None
        over, detail = self._user_over_limit(owner)
        if over:
            self._send_json(req, 429, {"error": "daily cost limit reached", **detail})
            return
        self.submit_ticket(ticket_id, message.strip(), owner_email=owner)
        self._send_json(req, 202, {"ticket_id": ticket_id})

    def _handle_list_tickets_group(self, req: BaseHTTPRequestHandler) -> None:
        if self.ticket_store is None:
            self._send_json(req, 404, {"error": "group chat not enabled"})
            return
        self._send_json(req, 200, {"tickets": self.ticket_store.list_tickets()})

    def _handle_get_ticket(
        self, req: BaseHTTPRequestHandler, ticket_id: str
    ) -> None:
        if self.ticket_store is None:
            self._send_json(req, 404, {"error": "group chat not enabled"})
            return
        events = [e.to_dict() for e in self.ticket_store.transcript(ticket_id)]
        self._send_json(req, 200, {"ticket_id": ticket_id, "events": events})

    def _handle_ticket_events(
        self, req: BaseHTTPRequestHandler, ticket_id: str
    ) -> None:
        self._serve_ticket_sse(req, ticket_id)

    def _handle_cancel_ticket(
        self, req: BaseHTTPRequestHandler, ticket_id: str
    ) -> None:
        """Emergency stop: cancel every agent running under this ticket — the
        coordinator and any in-flight specialists — and disable further
        auto-dispatch. The in-flight turn unwinds at the next tool / dispatch /
        ask_agent boundary; the next message clears the flag automatically."""
        if self.ticket_store is None:
            self._send_json(req, 404, {"error": "group chat not enabled"})
            return
        try:
            self.orchestrator.cancel_ticket(ticket_id)
        except AttributeError:
            return self._send_json(req, 501, {"error": "cancellation not supported"})
        # Record the stop on the transcript so the chat shows it settled.
        try:
            self.ticket_store.append(
                TicketEvent(
                    ticket_id=ticket_id,
                    actor="main",
                    kind="agent_message",
                    payload={"text": "Stopped by user — all agents in this turn cancelled.",
                             "status": "cancelled"},
                    task_id=ticket_id,
                )
            )
        except Exception:
            logger.warning("cancel ticket %s: transcript append failed", ticket_id)
        return self._send_json(req, 200, {"ticket_id": ticket_id, "cancelled": True})

    def _handle_close_ticket(
        self, req: BaseHTTPRequestHandler, ticket_id: str
    ) -> None:
        if self.ticket_store is None:
            self._send_json(req, 404, {"error": "group chat not enabled"})
            return
        try:
            entry = self.orchestrator.close_ticket(ticket_id)
        except Exception as exc:
            logger.exception("close ticket %s failed", ticket_id)
            self._send_json(req, 500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        summary = entry.summary if entry is not None else "(no memory configured)"
        # Record the closure on the transcript so the chat shows it settled.
        self.ticket_store.append(
            TicketEvent(
                ticket_id=ticket_id,
                actor="main",
                kind="agent_message",
                payload={"text": f"Ticket closed. {summary}", "status": "closed"},
                task_id=ticket_id,
            )
        )
        self._send_json(req, 200, {"ticket_id": ticket_id, "summary": summary})

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
                # AUD.5a: ticket linkage. Lets the chat page render the
                # approval card inline in the matching transcript and
                # the global toast broker suppress the popup when the
                # user is already viewing that ticket.
                "ticket_id": getattr(a, "ticket_id", None),
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
        # No bus publishes for UI-driven tool invocations — the audit log
        # (recorded inside gate_tools via ctx.audit) is the real record,
        # and the HTTP response carries the result. Publishing here just
        # bloated bus.log + polluted the live feed with /tools polling
        # noise (e.g. KubernetesPage auto-refresh).
        try:
            result = target.invoke(args)
        except Exception as exc:
            logger.exception("tool invocation %s.%s failed", agent_name, tool_name)
            self._send_json(req, 500, {
                "task_id": task_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
            return
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
        # No bus publish for UI-driven rollback execution — same reason as
        # the direct /tools handler above: audit log + HTTP response carry
        # everything the UI needs, no need to bloat bus.log.
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
            # by_agent comes from the per-agent telemetry (fed once per agent
            # run incl. dispatched specialists), NOT the task records — those
            # attribute a whole chat turn to "main". Snapshot under the lock.
            by_agent: dict[str, dict] = {a: dict(b) for a, b in self._agent_telemetry.items()}
        settled_statuses = {"success", "failed", "rejected", "cancelled"}
        settled = [t for t in tasks if t.status in settled_statuses]

        totals: dict = {
            "tasks": len(tasks),
            "settled": len(settled),
            "usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "wall_seconds": 0.0,
        }
        by_status: dict[str, int] = {}
        for t in tasks:
            by_status[t.status] = by_status.get(t.status, 0) + 1
        for t in settled:
            totals["usd"] += t.cost_usd or 0.0
            totals["input_tokens"] += t.input_tokens or 0
            totals["output_tokens"] += t.output_tokens or 0
            totals["wall_seconds"] += t.wall_seconds or 0.0

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

    # ---- ADM.2/3 — super-admin accounting ----

    @staticmethod
    def _utc_day_start(now: Optional[float] = None) -> float:
        """Unix-seconds floor of the current UTC day. Per-user daily
        spend resets at 00:00 UTC."""
        t = time.time() if now is None else now
        return t - (t % 86400.0)

    def _effective_limit(self, email: str) -> Optional[float]:
        """The daily cap actually enforced for a user: their explicit
        per-user limit if set, else the server-wide default (if any),
        else None (unlimited)."""
        explicit = self.user_limit_store.get(email)
        if explicit is not None:
            return explicit
        return self.default_user_daily_limit_usd

    def _user_over_limit(self, email: Optional[str]) -> tuple[bool, dict]:
        """Return (over, detail). Over when the user's effective daily
        cap (explicit override, else the server default) is set AND
        today's spend already meets/exceeds it. No email (bypass) ⇒
        never over. Reads spend from the durable ledger so the cap
        holds across restarts."""
        if not email:
            return False, {}
        cap = self._effective_limit(email)
        if cap is None:
            return False, {}
        spent = self.accounting.spend_today(email, self._utc_day_start())
        if spent >= cap:
            return True, {"spent_today_usd": round(spent, 4), "daily_limit_usd": cap}
        return False, {}

    def _require_admin(self, req: BaseHTTPRequestHandler) -> Optional[str]:
        """Return the admin's email, or send a 403 + return None. The
        auth gate already 401'd unauthenticated requests; this enforces
        the admin role on top."""
        session = self.auth.session_from_request(req.headers)
        email = session.email if session else None
        if not email or not self.auth.config.is_admin(email):
            self._send_json(req, 403, {"error": "super-admin only"})
            return None
        return email

    def _handle_admin_accounting(self, req: BaseHTTPRequestHandler) -> None:
        """Per-user accounting rollup, read from the durable ledger.
        Admin-only.

        {
          "users": [
            {"email", "tasks", "settled", "usd", "input_tokens",
             "output_tokens", "wall_seconds", "spent_today_usd",
             "daily_limit_usd": float|null,        # explicit override, null = uses default
             "effective_limit_usd": float|null}, ...# what's actually enforced
          ],
          "day_start_utc": float,
          "default_daily_limit_usd": float|null    # applies when no explicit override
        }
        """
        if self._require_admin(req) is None:
            return
        day_start = self._utc_day_start()
        rows = self.accounting.per_user(day_start)
        limits = self.user_limit_store.all()

        by_email = {r["email"]: r for r in rows}
        # Include users who have an explicit limit but no tasks yet,
        # so the admin sees + can adjust them.
        for email in limits:
            if email not in by_email:
                by_email[email] = {
                    "email": email, "tasks": 0, "settled": 0, "usd": 0.0,
                    "input_tokens": 0, "output_tokens": 0, "wall_seconds": 0.0,
                    "spent_today_usd": 0.0, "last_login_at": None, "login_count": 0,
                }

        for email, b in by_email.items():
            explicit = limits.get(email)
            b["daily_limit_usd"] = explicit
            b["effective_limit_usd"] = explicit if explicit is not None else self.default_user_daily_limit_usd

        payload = sorted(by_email.values(), key=lambda u: u["usd"], reverse=True)
        self._send_json(req, 200, {
            "users": payload,
            "day_start_utc": day_start,
            "default_daily_limit_usd": self.default_user_daily_limit_usd,
        })

    def _handle_admin_activity(self, req: BaseHTTPRequestHandler) -> None:
        """Recent cross-user activity feed (last 50 tasks, newest first),
        from the durable ledger. Admin-only."""
        if self._require_admin(req) is None:
            return
        self._send_json(req, 200, {
            "activity": self.accounting.recent_activity(50),
        })

    def _handle_admin_set_limit(self, req: BaseHTTPRequestHandler, email: str) -> None:
        """PUT /admin/limits/{email} — set or clear a user's daily cap.
        Body: {"daily_limit_usd": float | null}. Admin-only."""
        if self._require_admin(req) is None:
            return
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        raw = body.get("daily_limit_usd", None)
        limit: Optional[float]
        if raw is None:
            limit = None
        else:
            try:
                limit = float(raw)
            except (TypeError, ValueError):
                return self._send_json(req, 400, {"error": "daily_limit_usd must be a number or null"})
        try:
            self.user_limit_store.set(email, limit)
        except ValueError as exc:
            return self._send_json(req, 400, {"error": str(exc)})
        return self._send_json(req, 200, {
            "email": email.lower(),
            "daily_limit_usd": limit,
        })

    @staticmethod
    def _mcp_summary_dict(s: dict[str, Any]) -> dict[str, Any]:
        """Strip an internal registry record down to the JSON shape
        the UI consumes. Drops the live client + raw MCPServerConfig."""
        return {
            "name": s["name"],
            "target_agent": s.get("target_agent"),
            "command": s.get("command_summary"),
            "tool_count": len(s.get("tools", [])),
            "tools": [t["name"] for t in s.get("tools", [])],
            "destructive": sorted(s.get("destructive", [])),
            "status": s.get("status", "connected"),
            "error": s.get("error"),
        }

    def _handle_list_mcp_servers(self, req: BaseHTTPRequestHandler) -> None:
        """List MCP servers wired into the dashboard at startup.

        Returns ``{"servers": [<server-summary>, ...]}`` — no raw
        client/transport leaked to the wire."""
        with self._mcp_lock:
            servers = [self._mcp_summary_dict(s) for s in self.mcp_servers]
        self._send_json(req, 200, {"servers": servers})

    def _handle_add_mcp_server(self, req: BaseHTTPRequestHandler) -> None:
        """Register a new MCP server at runtime.

        SECURITY NOTE: ``command`` + ``args`` are executed as a
        subprocess on the dashboard host. Anyone with POST access
        to this endpoint can run arbitrary code on the host. The
        dashboard's threat model is single-user / intranet; multi-
        tenant deployments need auth (out of scope for v1).

        Body shape:
          {
            "name": "github",
            "target_agent": "programmer",
            "transport": "stdio" | "http",
            // stdio:
            "command": "npx",  "args": ["..."],  "env": {...},  "cwd": "..."?,
            // http:
            "url": "https://...",  "headers": {...},
            // both:
            "destructive": ["tool_name", ...]
          }

        Returns the new server's summary dict, or 4xx with an error
        message. Duplicate names return 409. Failed registration is
        added to the registry with ``status="error"`` and returned
        with a 200 so the UI can render the error card."""
        from agentlib import MCPServerConfig

        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        if not isinstance(body, dict):
            return self._send_json(req, 400, {"error": "body must be an object"})

        name = body.get("name")
        target = body.get("target_agent")
        transport = (body.get("transport") or "stdio").lower()
        if not name or not isinstance(name, str):
            return self._send_json(req, 400, {"error": "missing 'name'"})
        if not target or not isinstance(target, str):
            return self._send_json(req, 400, {"error": "missing 'target_agent'"})
        if transport not in ("stdio", "http"):
            return self._send_json(req, 400, {
                "error": "transport must be 'stdio' or 'http'",
            })
        if transport == "stdio" and not body.get("command"):
            return self._send_json(req, 400, {
                "error": "stdio transport requires 'command'",
            })
        if transport == "http" and not body.get("url"):
            return self._send_json(req, 400, {
                "error": "http transport requires 'url'",
            })

        with self._mcp_lock:
            if any(s["name"] == name for s in self.mcp_servers):
                return self._send_json(req, 409, {
                    "error": f"server {name!r} already registered",
                })

            config = MCPServerConfig(
                name=name,
                command=body.get("command", "") if transport == "stdio" else "",
                args=list(body.get("args") or []) if transport == "stdio" else [],
                env=dict(body.get("env") or {}) if transport == "stdio" else {},
                cwd=body.get("cwd") if transport == "stdio" else None,
                url=body.get("url", "") if transport == "http" else "",
                headers=dict(body.get("headers") or {}) if transport == "http" else {},
                destructive=set(body.get("destructive") or []),
            )
            by_name = {a.name: a for a in self.orchestrator.agents.values()}
            record = _register_one_mcp_server(
                {"name": name, "target_agent": target, "config": config},
                by_name,
            )
            self.mcp_servers.append(record)
            self._refresh_active_agents()
        return self._send_json(req, 200, self._mcp_summary_dict(record))

    def _handle_delete_mcp_server(
        self, req: BaseHTTPRequestHandler, name: str
    ) -> None:
        """Disconnect a previously-registered MCP server.

        Strips the server's prefixed tools off the target agent's
        ``tools`` + ``destructive_verbs``, closes the transport
        (best-effort), and removes the registry entry. Future tasks
        on that agent see the smaller tool set.

        Returns ``{"removed": true, "name": ...}`` or 404 if no
        such server is registered."""
        from urllib.parse import unquote

        name = unquote(name)
        by_name = {a.name: a for a in self.orchestrator.agents.values()}
        with self._mcp_lock:
            target_idx = next(
                (i for i, s in enumerate(self.mcp_servers) if s["name"] == name),
                None,
            )
            if target_idx is None:
                return self._send_json(req, 404, {
                    "error": f"no MCP server named {name!r}",
                })
            record = self.mcp_servers[target_idx]
            _unregister_one_mcp_server(record, by_name)
            self.mcp_servers.pop(target_idx)
            self._refresh_active_agents()
        return self._send_json(req, 200, {"removed": True, "name": name})

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

    # ---- inventory (Phase INV) ----

    @staticmethod
    def _host_to_jsonable(host: Any) -> dict[str, Any]:
        d = dataclasses.asdict(host)
        # Sort keys defensively so the wire shape is stable for tests.
        return d

    @staticmethod
    def _key_to_jsonable(key: Any) -> dict[str, Any]:
        """Strip the content side of the SshKey dataclass before serializing
        — we never put private-key bytes on the wire even if the dataclass
        is extended later. The InventoryStore protocol already gates this
        (content lives behind ``get_key_content``), but defense-in-depth."""
        d = dataclasses.asdict(key)
        d.pop("content", None)
        return d

    def _handle_list_hosts(self, req: BaseHTTPRequestHandler) -> None:
        hosts = self.inventory_store.list_hosts()
        self._send_json(req, 200, {"hosts": [self._host_to_jsonable(h) for h in hosts]})

    def _handle_post_host(self, req: BaseHTTPRequestHandler) -> None:
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        if not isinstance(body, dict):
            return self._send_json(req, 400, {"error": "body must be an object"})
        try:
            host = self.inventory_store.add_host(
                name=body.get("name", ""),
                address=body.get("address", ""),
                ssh_user=body.get("ssh_user") or "ubuntu",
                ssh_port=body.get("ssh_port") or 22,
                key_id=body.get("key_id") or None,
                groups=list(body.get("groups") or []),
                vars=dict(body.get("vars") or {}),
                description=body.get("description") or "",
            )
        except InventoryError as exc:
            return self._send_json(req, 400, {"error": str(exc)})
        return self._send_json(req, 201, {"host": self._host_to_jsonable(host)})

    def _handle_sync_terraform_inventory(self, req: BaseHTTPRequestHandler) -> None:
        """Operator action (admin-only): read a terraform stack's
        ``olympus_inventory_hosts`` output and seed each host into the runtime
        inventory (idempotent). NOT an agent tool — registering a host stays a
        human-gated step, so an agent that authored/applied the stack still
        can't expand its own reach without an operator running this.

        Body: {"working_dir": "<path to the applied terraform module>"}.
        Returns {"added": [...], "skipped": [...], "errors": [...]}.
        """
        import subprocess

        if self._require_admin(req) is None:
            return
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        working_dir = (body or {}).get("working_dir") if isinstance(body, dict) else None
        if not working_dir or not os.path.isdir(working_dir):
            return self._send_json(req, 400, {"error": "working_dir missing or not a directory"})

        try:
            proc = subprocess.run(
                ["terraform", f"-chdir={working_dir}", "output", "-json", "olympus_inventory_hosts"],
                capture_output=True, text=True, timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return self._send_json(req, 500, {"error": f"terraform invocation failed: {exc}"})
        if proc.returncode != 0:
            return self._send_json(req, 400, {
                "error": "terraform output failed (no olympus_inventory_hosts output, or stack not applied)",
                "detail": (proc.stderr or "").strip()[:500],
            })
        try:
            hosts = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "olympus_inventory_hosts output was not valid JSON"})
        if not isinstance(hosts, list):
            return self._send_json(req, 400, {"error": "olympus_inventory_hosts must be a list"})

        key_ids = {k.name: k.id for k in self.inventory_store.list_keys()}
        added: list[str] = []
        skipped: list[str] = []
        errors: list[dict[str, str]] = []
        for h in hosts:
            if not isinstance(h, dict) or not h.get("name"):
                errors.append({"host": str(h)[:60], "error": "entry missing a name"})
                continue
            name = h["name"]
            key_name = h.get("key") or None
            key_id = key_ids.get(key_name) if key_name else None
            if key_name and key_id is None:
                errors.append({"host": name, "error": f"key '{key_name}' not in the store"})
                continue
            try:
                self.inventory_store.add_host(
                    name=name,
                    address=h.get("address", ""),
                    ssh_user=h.get("ssh_user") or "ubuntu",
                    ssh_port=int(h.get("ssh_port") or 22),
                    key_id=key_id,
                    groups=list(h.get("groups") or []),
                    vars=dict(h.get("vars") or {}),
                    description=h.get("description") or "synced from terraform",
                )
                added.append(name)
            except InventoryError as exc:
                if "already exists" in str(exc):
                    skipped.append(name)
                else:
                    errors.append({"host": name, "error": str(exc)})
        return self._send_json(req, 200, {"added": added, "skipped": skipped, "errors": errors})

    def _handle_put_host(self, req: BaseHTTPRequestHandler, host_id: str) -> None:
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        if not isinstance(body, dict):
            return self._send_json(req, 400, {"error": "body must be an object"})
        # Only forward the fields the user actually sent — partial update.
        updates: dict[str, Any] = {}
        for k in ("name", "address", "ssh_user", "ssh_port", "key_id",
                  "groups", "vars", "description"):
            if k in body:
                updates[k] = body[k]
        try:
            host = self.inventory_store.update_host(host_id, **updates)
        except InventoryError as exc:
            msg = str(exc)
            status = 404 if "not found" in msg else 400
            return self._send_json(req, status, {"error": msg})
        return self._send_json(req, 200, {"host": self._host_to_jsonable(host)})

    def _handle_delete_host(self, req: BaseHTTPRequestHandler, host_id: str) -> None:
        ok = self.inventory_store.remove_host(host_id)
        if not ok:
            return self._send_json(req, 404, {"error": "host not found"})
        return self._send_json(req, 200, {"ok": True})

    def _handle_list_keys(self, req: BaseHTTPRequestHandler) -> None:
        keys = self.inventory_store.list_keys()
        self._send_json(req, 200, {"keys": [self._key_to_jsonable(k) for k in keys]})

    def _handle_post_key(self, req: BaseHTTPRequestHandler) -> None:
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        if not isinstance(body, dict):
            return self._send_json(req, 400, {"error": "body must be an object"})
        try:
            key = self.inventory_store.add_key(
                name=body.get("name", ""),
                content=body.get("content", ""),
            )
        except InventoryError as exc:
            return self._send_json(req, 400, {"error": str(exc)})
        return self._send_json(req, 201, {"key": self._key_to_jsonable(key)})

    def _handle_delete_key(self, req: BaseHTTPRequestHandler, key_id: str) -> None:
        try:
            ok = self.inventory_store.remove_key(key_id)
        except InventoryError as exc:
            return self._send_json(req, 409, {"error": str(exc)})
        if not ok:
            return self._send_json(req, 404, {"error": "key not found"})
        return self._send_json(req, 200, {"ok": True})

    def _handle_render_inventory(self, req: BaseHTTPRequestHandler) -> None:
        """Render the current store as an ansible INI inventory text
        for preview in the UI. Does NOT include key file paths — the UI
        only needs the structure; the ansible agent materializes a
        full run-dir with keys when it actually runs."""
        hosts = self.inventory_store.list_hosts()
        text = render_ansible_inventory(hosts)
        encoded = text.encode("utf-8")
        req.send_response(200)
        req.send_header("Content-Type", "text/plain; charset=utf-8")
        req.send_header("Content-Length", str(len(encoded)))
        req.end_headers()
        req.wfile.write(encoded)

    # ---- terminal (TERM.2a) ----

    @staticmethod
    def _terminal_info_to_jsonable(info: Any) -> dict[str, Any]:
        return dataclasses.asdict(info)

    def _owner_email(self, req: BaseHTTPRequestHandler) -> Optional[str]:
        """Resolve the requesting user's email via the existing session
        cookie. Returns None only if the route was wrongly left ungated
        (defense-in-depth — gated routes already 401 above)."""
        sess = self.auth.session_from_request(req.headers)
        return sess.email if sess is not None else None

    def _handle_list_terminal_sessions(self, req: BaseHTTPRequestHandler) -> None:
        owner = self._owner_email(req)
        if owner is None:
            return self._send_json(req, 401, {"error": "unauthenticated"})
        # Reap stale sessions opportunistically so the list reflects truth.
        self.session_manager.tick_expiry()
        sessions = self.session_manager.list_for_user(owner)
        payload = {
            "sessions": [
                self._terminal_info_to_jsonable(session_to_info(s))
                for s in sessions
            ],
        }
        return self._send_json(req, 200, payload)

    def _handle_post_terminal_session(self, req: BaseHTTPRequestHandler) -> None:
        """Create a new pty session — two shapes:

        SSH (default): ``{host_alias, ssh_user?}``. Looks up the host
        in the inventory store, materializes its key (if any), spawns
        the ssh subprocess.

        Local CLI (TERM.9a): ``{kind: "olympus-tui"}`` (or another
        allowed kind). Spawns the local CLI inside the dashboard pod —
        no ssh, no host lookup, no key. Useful for driving Olympus
        itself from a terminal session.

        Returns SessionInfo + the WebSocket URL the frontend will dial.
        """
        owner = self._owner_email(req)
        if owner is None:
            return self._send_json(req, 401, {"error": "unauthenticated"})
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        if not isinstance(body, dict):
            return self._send_json(req, 400, {"error": "body must be an object"})

        kind = (body.get("kind") or "").strip() or None
        if kind is not None:
            # Local CLI path. host_alias becomes a display label;
            # ssh_user / address get sentinel values.
            try:
                session = self.session_manager.create(
                    owner_email=owner,
                    host_alias=kind,
                    ssh_user="",
                    address="(local)",
                    kind=kind,
                )
            except (ValueError, FileNotFoundError) as exc:
                return self._send_json(req, 400, {"error": str(exc)})
            except Exception as exc:
                logger.exception("terminal session (local kind=%s) create failed", kind)
                return self._send_json(req, 500, {
                    "error": f"{type(exc).__name__}: {exc}",
                })
            info = self._terminal_info_to_jsonable(session_to_info(session))
            info["ws_url"] = f"/terminal/sessions/{session.session_id}/ws"
            return self._send_json(req, 201, {"session": info})

        alias = (body.get("host_alias") or "").strip()
        if not alias:
            return self._send_json(req, 400, {"error": "host_alias is required"})
        host = self.inventory_store.get_host_by_name(alias)
        if host is None:
            return self._send_json(req, 404, {"error": f"unknown host alias {alias!r}"})

        ssh_user_override = (body.get("ssh_user") or "").strip()
        effective_user = ssh_user_override or host.ssh_user

        # Pull key content if the host references one. A missing key
        # body (host points at a deleted key) is a 422 — the operator's
        # inventory is internally inconsistent.
        key_content: Optional[str] = None
        if host.key_id:
            key_content = self.inventory_store.get_key_content(host.key_id)
            if key_content is None:
                return self._send_json(req, 422, {
                    "error": f"host {alias!r} references key_id {host.key_id!r} "
                             "but its content is missing from the store",
                })

        try:
            session = self.session_manager.create(
                owner_email=owner,
                host_alias=host.name,
                ssh_user=effective_user,
                address=host.address,
                key_content=key_content,
                ssh_port=host.ssh_port,
            )
        except Exception as exc:
            logger.exception("terminal session create failed")
            return self._send_json(req, 500, {
                "error": f"{type(exc).__name__}: {exc}",
            })

        info = self._terminal_info_to_jsonable(session_to_info(session))
        info["ws_url"] = f"/terminal/sessions/{session.session_id}/ws"
        return self._send_json(req, 201, {"session": info})

    def _handle_terminal_ws(
        self, req: BaseHTTPRequestHandler, session_id: str,
    ) -> None:
        """Upgrade HTTP → WebSocket and bridge the connection to the
        session's pty. Blocks until either side closes.

        Auth gate runs in do_GET above (the /terminal prefix is gated);
        here we re-check ownership so an attacker who got a session id
        but isn't the owner can't attach via WS."""
        owner = self._owner_email(req)
        if owner is None:
            return self._send_json(req, 401, {"error": "unauthenticated"})
        session = self.session_manager.get(session_id)
        if session is None or session.owner_email != owner:
            return self._send_json(req, 404, {"error": "session not found"})

        try:
            ws = perform_ws_handshake(
                requestline=req.requestline,
                headers=req.headers,
                write_response=req.wfile.write,
            )
        except WSHandshakeError as exc:
            return self._send_json(req, 400, {"error": f"ws upgrade failed: {exc}"})

        # The underlying TCP socket is hijacked from here on — let the
        # bridge own all reads/writes until close.
        try:
            bridge_session_to_socket(
                session=session,
                manager=self.session_manager,
                sock=req.connection,
                ws=ws,
            )
        except Exception:
            logger.exception("terminal ws bridge crashed for %s", session_id)

    def _handle_ask_terminal_session(
        self, req: BaseHTTPRequestHandler, session_id: str,
    ) -> None:
        """TERM.4b — one-shot Q&A with the terminal_companion agent
        scoped to a single live session. Body: ``{question}``.
        Returns ``{answer, suggested_commands}``.

        Owner check applies — non-owners get 404, not 403, so session
        ids stay un-probable. terminal_companion's tool re-checks
        ownership inside the closure too, but defense-in-depth.
        """
        owner = self._owner_email(req)
        if owner is None:
            return self._send_json(req, 401, {"error": "unauthenticated"})
        session = self.session_manager.get(session_id)
        if session is None or session.owner_email != owner:
            return self._send_json(req, 404, {"error": "session not found"})
        try:
            body = self._read_json(req)
        except json.JSONDecodeError:
            return self._send_json(req, 400, {"error": "invalid JSON"})
        if not isinstance(body, dict):
            return self._send_json(req, 400, {"error": "body must be an object"})
        question = (body.get("question") or "").strip()
        if not question:
            return self._send_json(req, 400, {"error": "question is required"})

        # Build a minimal AgentContext on demand. terminal_companion has
        # no destructive verbs so the approval hook never fires; we
        # still pass the dashboard's QueueApprovalHook + JsonlAuditLogger
        # so tool calls land in the same audit stream as every other
        # gated tool call.
        from agentlib import JsonlAuditLogger
        from terminal_companion import ask as companion_ask

        # terminal_companion has no destructive/self-targeting tools, so the
        # policy is a no-op here — included for parity + forward-safety if a
        # future tool is added. from_env() is a cheap env read.
        ctx = AgentContext(
            approval=self.approval_hook,
            audit=JsonlAuditLogger(self.audit_log_path),
            self_protection=SelfProtectionPolicy.from_env(),
        )
        try:
            resp = companion_ask(
                question=question,
                session_manager=self.session_manager,
                session_id=session_id,
                owner_email=owner,
                ctx=ctx,
            )
        except Exception as exc:
            logger.exception("terminal_companion ask failed")
            return self._send_json(req, 500, {
                "error": f"{type(exc).__name__}: {exc}",
            })
        return self._send_json(req, 200, {
            "answer": resp.answer,
            "suggested_commands": list(resp.suggested_commands),
        })

    def _handle_delete_terminal_session(
        self, req: BaseHTTPRequestHandler, session_id: str,
    ) -> None:
        owner = self._owner_email(req)
        if owner is None:
            return self._send_json(req, 401, {"error": "unauthenticated"})
        session = self.session_manager.get(session_id)
        if session is None:
            return self._send_json(req, 404, {"error": "session not found"})
        if session.owner_email != owner:
            # Don't leak existence to non-owners; mimic 404.
            return self._send_json(req, 404, {"error": "session not found"})
        self.session_manager.close(session_id)
        return self._send_json(req, 200, {"ok": True})

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

    def _serve_ticket_sse(self, req: BaseHTTPRequestHandler, ticket_id: str) -> None:
        """Stream a ticket's group-chat transcript. The transcript is the
        unified record (human + agent messages, dispatches, tool calls,
        ask_agent exchanges), so we tail the ticket store by monotonic
        ``seq`` rather than the bus — the bus misses directly-written
        events. Polling is fine at single-dashboard volume."""
        if self.ticket_store is None:
            req.send_response(404)
            req.end_headers()
            return
        req.send_response(200)
        req.send_header("Content-Type", "text/event-stream")
        req.send_header("Cache-Control", "no-cache")
        req.send_header("X-Accel-Buffering", "no")
        req.end_headers()

        last_seq = 0
        last_heartbeat = time.monotonic()
        while True:
            new_events = self.ticket_store.transcript(ticket_id, after_seq=last_seq)
            for ev in new_events:
                if not _send_sse_ticket_event(req, ev):
                    return  # client disconnected
                last_seq = ev.seq
            now = time.monotonic()
            if now - last_heartbeat > 15.0:
                try:
                    req.wfile.write(b": heartbeat\n\n")
                    req.wfile.flush()
                    last_heartbeat = now
                except (ConnectionError, BrokenPipeError):
                    return
            time.sleep(0.6)

    def _publish_stream(self, ticket_id: str, chunk: str) -> None:
        """token_sink: fan a reply chunk out to this ticket's live /stream
        subscribers. Best-effort + ephemeral — never persisted."""
        with self._stream_lock:
            subs = list(self._stream_subs.get(ticket_id, []))
        for q in subs:
            try:
                q.put_nowait(chunk)
            except Exception:
                pass

    def _serve_token_stream(self, req: BaseHTTPRequestHandler, ticket_id: str) -> None:
        """SSE of the main agent's reply tokens for a ticket as they generate.
        Ephemeral: the persisted transcript still carries the final reply as a
        single agent_message; the frontend swaps the live buffer for it."""
        import queue as _queue

        q: "_queue.Queue[str]" = _queue.Queue()
        with self._stream_lock:
            self._stream_subs.setdefault(ticket_id, []).append(q)

        req.send_response(200)
        req.send_header("Content-Type", "text/event-stream")
        req.send_header("Cache-Control", "no-cache")
        req.send_header("X-Accel-Buffering", "no")
        req.end_headers()
        last_heartbeat = time.monotonic()
        try:
            while True:
                try:
                    chunk = q.get(timeout=1.0)
                    req.wfile.write(f"data: {json.dumps({'chunk': chunk})}\n\n".encode())
                    req.wfile.flush()
                except _queue.Empty:
                    if time.monotonic() - last_heartbeat > 15.0:
                        req.wfile.write(b": heartbeat\n\n")
                        req.wfile.flush()
                        last_heartbeat = time.monotonic()
        except (ConnectionError, BrokenPipeError, OSError):
            pass  # client disconnected
        finally:
            with self._stream_lock:
                try:
                    self._stream_subs.get(ticket_id, []).remove(q)
                except ValueError:
                    pass

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
        "ticket_id": getattr(msg, "ticket_id", None),
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


def _send_sse_ticket_event(req: BaseHTTPRequestHandler, ev: TicketEvent) -> bool:
    """Write one group-chat transcript event as an SSE data line."""
    line = f"data: {json.dumps(ev.to_dict(), default=str)}\n\n".encode("utf-8")
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
    ticket_store = InMemoryTicketStore()
    # DEMO_MODE (Phase D): on a public demo, every destructive verb is
    # auto-rejected so a curious reviewer can't `delete_pod` / `tf_apply`
    # even if they try to approve. AlwaysReject ships in agentlib.
    demo_mode = (os.environ.get("OLYMPUS_DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on"))
    if demo_mode:
        logger.warning("OLYMPUS_DEMO_MODE=1 — destructive tools are auto-rejected.")
        approval_hook = AlwaysReject()
    else:
        approval_hook = QueueApprovalHook()
    if rollback is None and os.environ.get("OLYMPUS_ROLLBACK", "").lower() != "disabled":
        rollback_log_path = rollback_log_path or str(
            Path(audit_log_path).with_name("rollback.jsonl")
        )
        rollback = JsonlRollbackStore(rollback_log_path)
    # Daily LLM-cost cap (Phase D). When OLYMPUS_DAILY_COST_CAP_USD is set,
    # build a BudgetGuard the agents pass to StructuralAgent; .invoke()
    # gates on the cumulative cost across the process via cost_getter.
    budget_guard = None
    daily_cap_raw = os.environ.get("OLYMPUS_DAILY_COST_CAP_USD", "").strip()
    if daily_cap_raw:
        try:
            cap = float(daily_cap_raw)
            if cap > 0:
                budget_guard = BudgetGuard(
                    max_budget=cap,
                    cost_getter=lambda: float(get_cost_for_type("all")[0] or 0.0),
                )
                logger.info("BudgetGuard active — daily cap $%.2f", cap)
        except ValueError:
            logger.warning("Invalid OLYMPUS_DAILY_COST_CAP_USD=%r — ignoring", daily_cap_raw)
    # Inventory store: file-backed on disk, default sibling of the audit log.
    # The chart mounts /var/lib/olympus as the audit volume; inventory.json
    # lands there too so a single PVC handles both. Construct it BEFORE
    # the AgentContext so the agents see it.
    inventory_path = os.environ.get("OLYMPUS_INVENTORY_PATH", "").strip() or str(
        Path(audit_log_path).with_name("inventory.json")
    )
    inventory_store: InventoryStore = FileBackedInventoryStore(inventory_path)
    # ADM.3 — per-user daily cost caps, persisted next to inventory on
    # the same PVC.
    user_limit_store: UserLimitStore = FileBackedUserLimitStore(
        str(Path(inventory_path).with_name("user_limits.json"))
    )
    # ACCT-SQL.1 — durable accounting ledger on the same volume. Survives
    # pod restarts when /var/lib/olympus is a PVC (the demo enables this).
    accounting_store = SqliteAccountingStore(
        str(Path(inventory_path).with_name("accounting.db"))
    )
    # Per-user daily cap default (applies when an admin hasn't set an
    # explicit one). Empty/unset = no default (unlimited unless set).
    _dflt_raw = os.environ.get("OLYMPUS_DEFAULT_USER_DAILY_LIMIT_USD", "").strip()
    default_user_daily_limit_usd: Optional[float] = None
    if _dflt_raw:
        try:
            v = float(_dflt_raw)
            default_user_daily_limit_usd = v if v > 0 else None
        except ValueError:
            logger.warning("Invalid OLYMPUS_DEFAULT_USER_DAILY_LIMIT_USD=%r — ignoring", _dflt_raw)
    # Self-protection: hard-block the agents from managing the cluster / VM
    # hosts Olympus runs on (see agentlib.SelfProtectionPolicy). Identity comes
    # from OLYMPUS_SELF_NAMESPACE (downward API) + OLYMPUS_SELF_NODES, never the
    # user-editable inventory. Disabled (no-op) on dev where neither is set.
    self_protection = SelfProtectionPolicy.from_env()
    if self_protection.enabled:
        logger.info(
            "Self-protection ON — namespace=%s protected=%s self_nodes=%s",
            self_protection.self_namespace,
            sorted(self_protection.protected_namespaces),
            sorted(self_protection.self_nodes),
        )
    else:
        logger.info("Self-protection OFF — no OLYMPUS_SELF_NAMESPACE/OLYMPUS_SELF_NODES configured")
    ctx = AgentContext(
        approval=approval_hook,
        audit=JsonlAuditLogger(audit_log_path),
        rollback=rollback,
        budget_guard=budget_guard,
        inventory_store=inventory_store,
        self_protection=self_protection,
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
    # The generalist main agent coordinates group-chat tickets. It is a
    # dispatch target (routable=False) so the LLMRouter never picks it for a
    # standalone task; only the chat/ticket path invokes it.
    from main_agent.agent import MainAgent

    agents.append(MainAgent())
    # Startup MCP servers: explicit param wins; else parse the
    # OLYMPUS_MCP_SERVERS env (JSON). Declaring them here means they're
    # wired on every boot — no post-deploy curl ritual, and they survive
    # pod restarts (the in-memory runtime registry is rebuilt at startup).
    if mcp_servers is None:
        mcp_servers = _mcp_servers_from_env()
    mcp_registry = _wire_mcp_servers(agents, mcp_servers or [])
    orch = build_orchestrator(
        ctx=ctx,
        agents=agents,
        router=router,
        bus=bus,
        memory=memory,
        ticket_store=ticket_store,
    )
    # Auth (Phase A): config from env; AUTH_BYPASS=1 disables auth for
    # local dev. In a public deploy, set OLYMPUS_AUTH_GOOGLE_CLIENT_ID/SECRET
    # + ALLOWED_DOMAINS + SESSION_SECRET via the olympus-secrets secret.
    auth_cfg = AuthConfig.from_env()
    authenticator = Authenticator(auth_cfg)
    server = DashboardServer(
        orchestrator=orch,
        bus=bus,
        approval_hook=approval_hook,
        audit_log_path=audit_log_path,
        host=host,
        port=port,
        mcp_servers=mcp_registry,
        ticket_store=ticket_store,
        auth=authenticator,
        inventory_store=inventory_store,
        user_limit_store=user_limit_store,
        accounting_store=accounting_store,
        default_user_daily_limit_usd=default_user_daily_limit_usd,
    )
    # Wire per-agent cost telemetry: the orchestrator reads ctx.cost_sink
    # lazily at run time, so mutating the shared ctx after construction is
    # safe and avoids a chicken-and-egg with the server instance.
    ctx.cost_sink = server._record_agent_cost
    # Wire reply-token streaming: the main agent emits (ticket_id, chunk) to
    # this sink, which fans out to the ticket's /stream SSE subscribers.
    ctx.token_sink = server._publish_stream
    return server


def _mcp_command_summary(config: Any) -> str:
    """One-line human summary for the server card. HTTP and stdio
    transports format differently so the UI can pick the right
    prefix glyph."""
    if getattr(config, "url", ""):
        return f"HTTP {config.url}"
    if getattr(config, "command", ""):
        return f"{config.command} {' '.join(config.args)}".strip()
    return ""


def _register_one_mcp_server(
    entry: dict[str, Any], by_name: dict[str, Any],
) -> dict[str, Any]:
    """Register a single MCP server's tools onto its target agent
    and return one registry-record dict.

    ``entry``: ``{name, target_agent, config, client?}``. ``config``
    is an ``MCPServerConfig`` instance. If ``client`` is None, the
    right transport is built by ``build_transport``; pass an explicit
    client in tests to use ``MockTransport``.

    Failures are caught and turned into ``status="error"`` records —
    the caller decides whether to surface them or roll back."""
    from agentlib import MCPClient, build_transport, register_mcp_tools

    name = entry["name"]
    target = entry.get("target_agent")
    config = entry["config"]
    client = entry.get("client")
    record: dict[str, Any] = {
        "name": name,
        "target_agent": target,
        "command_summary": _mcp_command_summary(config),
        "destructive": set(getattr(config, "destructive", set())),
        "status": "connected",
        "tools": [],
        "error": None,
        # The live client is kept inside the record so unregister can
        # close the transport. NOT exposed by the JSON wire format.
        "_client": None,
        "_config": config,
    }
    if target not in by_name:
        record["status"] = "error"
        record["error"] = f"unknown target_agent {target!r}"
        return record
    try:
        if client is None:
            client = MCPClient(build_transport(config))
            client.initialize()
        tools = client.list_tools()
        register_mcp_tools(by_name[target], config, client=client)
        record["tools"] = tools
        record["_client"] = client
    except Exception as exc:
        logger.warning("MCP server %r registration failed: %s", name, exc)
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def _unregister_one_mcp_server(
    record: dict[str, Any], by_name: dict[str, Any],
) -> None:
    """Remove a server's prefixed tools from its target agent's tool
    list and destructive_verbs, then close the transport.

    Safe to call on error-state records (they have no tools or
    client). Best-effort on transport.close() — a hang there
    shouldn't block the operator from disconnecting."""
    name = record["name"]
    target = record.get("target_agent")
    config = record.get("_config")
    prefix = name  # register_mcp_tools used name as prefix by default

    if target in by_name and config is not None:
        agent = by_name[target]
        # Drop the server's prefixed tools.
        agent.tools = [
            t for t in agent.tools
            if not t.name.startswith(f"{prefix}_")
        ]
        # Drop the server's prefixed destructive verbs.
        agent.destructive_verbs = {
            v for v in agent.destructive_verbs
            if not v.startswith(f"{prefix}_")
        }

    client = record.get("_client")
    if client is not None:
        try:
            client.close()
        except Exception as exc:
            logger.warning("MCP %r close failed: %s", name, exc)


def _mcp_servers_from_env() -> list[dict[str, Any]]:
    """Parse OLYMPUS_MCP_SERVERS — a JSON array of server specs, each the
    same shape POST /mcp/servers accepts:
      {"name","target_agent","transport":"http"|"stdio",
       "url"|"command", "args","env","headers","destructive"}
    Returns the list of {name, target_agent, config: MCPServerConfig}
    entries _wire_mcp_servers consumes. Malformed entries are skipped
    with a warning so one bad spec doesn't sink startup."""
    raw = os.environ.get("OLYMPUS_MCP_SERVERS", "").strip()
    if not raw:
        return []
    from agentlib import MCPServerConfig

    try:
        specs = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("OLYMPUS_MCP_SERVERS is not valid JSON — ignoring: %s", exc)
        return []
    if not isinstance(specs, list):
        logger.warning("OLYMPUS_MCP_SERVERS must be a JSON array — ignoring")
        return []

    out: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        target = spec.get("target_agent")
        transport = (spec.get("transport") or "stdio").lower()
        if not name or not target:
            logger.warning("OLYMPUS_MCP_SERVERS entry missing name/target_agent — skipping: %r", spec)
            continue
        if transport == "http" and not spec.get("url"):
            logger.warning("OLYMPUS_MCP_SERVERS http entry %r missing url — skipping", name)
            continue
        if transport == "stdio" and not spec.get("command"):
            logger.warning("OLYMPUS_MCP_SERVERS stdio entry %r missing command — skipping", name)
            continue
        config = MCPServerConfig(
            name=name,
            command=spec.get("command", "") if transport == "stdio" else "",
            args=list(spec.get("args") or []) if transport == "stdio" else [],
            env=dict(spec.get("env") or {}) if transport == "stdio" else {},
            cwd=spec.get("cwd") if transport == "stdio" else None,
            url=spec.get("url", "") if transport == "http" else "",
            headers=dict(spec.get("headers") or {}) if transport == "http" else {},
            destructive=set(spec.get("destructive") or []),
        )
        out.append({"name": name, "target_agent": target, "config": config})
    return out


def _wire_mcp_servers(
    agents: list[Any], configs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Register every MCP server at dashboard startup. Per-server
    failures are isolated — other servers still register."""
    by_name = {a.name: a for a in agents}
    return [_register_one_mcp_server(entry, by_name) for entry in configs]
