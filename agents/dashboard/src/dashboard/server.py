"""
Olympus dashboard backend (W5-6).

Stdlib HTTP server. Same family of decisions as ``WebhookApprovalHook``:
no FastAPI/Flask. The bus is the source of truth for live activity;
the dashboard is a thin SSE bridge over it plus the approval queue.

Endpoints
---------

- ``GET /``                       — static index.html (the UI).
- ``POST /tasks``                 — body: ``{natural_language, router?}``,
                                    returns ``{task_id}``. Submission runs
                                    in a worker thread; the response is
                                    immediate.
- ``GET /tasks``                  — list known task ids + status.
- ``GET /tasks/{id}``             — final result (``404`` if unknown,
                                    ``202`` while in flight).
- ``GET /tasks/{id}/events``      — SSE stream of bus messages for the
                                    task.
- ``GET /events``                 — SSE stream of every bus message
                                    (broadcast / "*" subscriber).
- ``GET /approvals``              — list pending approvals.
- ``POST /approvals/{id}``        — body: ``{approved, reason,
                                    modified_args?}``. Resolves a
                                    pending approval.
- ``GET /audit``                  — JSONL audit log download.
- ``GET /healthz``                — liveness check.

This module is import-safe even when the LLM stack and the agent
packages are not installed — the orchestrator + agents are passed in by
the caller.
"""
from __future__ import annotations

import dataclasses
import json
import logging
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
    InMemoryBus,
    JsonlAuditLogger,
    Orchestrator,
    PendingApproval,
    QueueApprovalHook,
    Router,
    TaskMessage,
)

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
    ):
        self.orchestrator = orchestrator
        self.bus = bus
        self.approval_hook = approval_hook
        self.audit_log_path = audit_log_path
        self.static_dir = static_dir or Path(__file__).resolve().parent.parent.parent / "static"

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
            else:
                rec.status = getattr(payload, "status", "success")
                rec.result_summary = getattr(payload, "summary", None)
                rec.result_artifacts = getattr(payload, "artifacts", None)

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
                if path.startswith("/static/"):
                    return outer._serve_static(self, path[len("/static/"):])
                self.send_response(404)
                self.end_headers()

            def do_POST(self):  # noqa: N802
                if self.path == "/tasks":
                    return outer._handle_post_task(self)
                if self.path.startswith("/approvals/"):
                    return outer._handle_resolve_approval(
                        self, self.path[len("/approvals/"):]
                    )
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
        path = self.static_dir / name
        if not path.is_file() or self.static_dir.resolve() not in path.resolve().parents and path.resolve() != (self.static_dir / name).resolve():
            req.send_response(404)
            req.end_headers()
            return
        ext = path.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json",
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
) -> DashboardServer:
    """Construct a DashboardServer with the four production agents and
    an in-memory bus. Convenience for the CLI entry point.
    """
    from olympus_cli.registry import build_orchestrator, default_agents

    bus = InMemoryBus()
    approval_hook = QueueApprovalHook()
    ctx = AgentContext(
        approval=approval_hook,
        audit=JsonlAuditLogger(audit_log_path),
    )
    orch = build_orchestrator(
        ctx=ctx, agents=default_agents(), router=router, bus=bus
    )
    return DashboardServer(
        orchestrator=orch,
        bus=bus,
        approval_hook=approval_hook,
        audit_log_path=audit_log_path,
        host=host,
        port=port,
    )
