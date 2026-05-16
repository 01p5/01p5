#!/usr/bin/env python3
"""
Demo MCP server for Olympus.

Tiny, self-contained, dependency-free: speaks JSON-RPC 2.0 over
stdio per the Model Context Protocol spec (2024-11-05). Designed to
be plugged into Olympus's MCPClient + dashboard so the W9-10 demo
has a real third-party server to integrate against.

Three tools, picked to demonstrate the gating story:

  counter_increment(by: int = 1) -> int
      Increment a process-local counter. Read-only-feeling but
      mutates server state — useful for showing that the server
      itself decides what's stateful.

  notes_append(text: str) -> int
      Append a note to a process-local list. *Destructive* —
      register with ``destructive={"notes_append"}`` so Olympus
      routes it through ApprovalHook before each call.

  notes_list() -> str
      Return all notes joined with newlines. Read-only.

Run on its own (Ctrl+C to stop) — it'll sit on stdin waiting for
JSON-RPC requests:

    python3 infra/demo-mcp-server/server.py

Or wire it into Olympus via ``mcp_servers=`` in build_default_server:

    from agentlib import MCPServerConfig
    cfg = MCPServerConfig(
        name="demo",
        command="python3",
        args=["infra/demo-mcp-server/server.py"],
        destructive={"notes_append"},
    )
    server = build_default_server(mcp_servers=[
        {"name": "demo", "target_agent": "programmer", "config": cfg},
    ])

After registration, the agent sees ``demo_counter_increment``,
``demo_notes_append``, ``demo_notes_list`` alongside its native
tools. The destructive one re-prompts the human via the approval
queue.

No external Python deps — only stdlib. Run with python3 ≥ 3.8.
"""
from __future__ import annotations

import json
import sys
import threading
from typing import Any, Callable


PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "olympus-demo-mcp", "version": "0.1.0"}


# ---------------------------------------------------------------------
# Tools — process-local state, so each `python server.py` invocation
# has a fresh notebook + counter. That's intentional for a demo:
# nothing persists, so the demo is reproducible.
# ---------------------------------------------------------------------


_state_lock = threading.Lock()
_counter = 0
_notes: list[str] = []


def tool_counter_increment(arguments: dict[str, Any]) -> dict[str, Any]:
    global _counter
    by = int(arguments.get("by", 1))
    with _state_lock:
        _counter += by
        value = _counter
    return _text_result(f"counter is now {value}")


def tool_notes_append(arguments: dict[str, Any]) -> dict[str, Any]:
    text = arguments.get("text")
    if not isinstance(text, str) or not text.strip():
        return _error_result("notes_append: 'text' is required")
    with _state_lock:
        _notes.append(text)
        position = len(_notes)
    return _text_result(f"appended note #{position}: {text}")


def tool_notes_list(arguments: dict[str, Any]) -> dict[str, Any]:
    with _state_lock:
        snapshot = list(_notes)
    if not snapshot:
        return _text_result("(no notes yet)")
    return _text_result(
        "\n".join(f"{i + 1}. {n}" for i, n in enumerate(snapshot))
    )


TOOLS: list[dict[str, Any]] = [
    {
        "name": "counter_increment",
        "description": "Increment a server-side counter and return its new value.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "by": {"type": "integer", "default": 1, "description": "How much to add (default 1)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "notes_append",
        "description": "Append a note to the server's notebook. Destructive — once written, the demo never deletes notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Note body."},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "notes_list",
        "description": "Return every note in the order they were appended.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]

HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "counter_increment": tool_counter_increment,
    "notes_append": tool_notes_append,
    "notes_list": tool_notes_list,
}


# ---------------------------------------------------------------------
# JSON-RPC envelope helpers
# ---------------------------------------------------------------------


def _text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _error_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _ok(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------
# Request dispatch
# ---------------------------------------------------------------------


def dispatch(request: dict[str, Any]) -> dict[str, Any] | None:
    """Return the response envelope, or None for notifications (no
    response required by the spec)."""
    method = request.get("method")
    msg_id = request.get("id")
    params = request.get("params") or {}

    # Notifications: no id, no response. The only one we expect is
    # ``notifications/initialized`` — ignore it (and any other
    # well-formed notification) silently.
    if msg_id is None:
        return None

    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _ok(msg_id, _error_result(f"unknown tool {name!r}"))
        try:
            return _ok(msg_id, handler(arguments))
        except Exception as exc:
            return _ok(msg_id, _error_result(
                f"{type(exc).__name__}: {exc}"
            ))

    return _err(msg_id, -32601, f"method not found: {method}")


# ---------------------------------------------------------------------
# Main loop — readline / writeline on stdin/stdout. One JSON object
# per line in both directions. Lines longer than the OS buffer get
# split by readline; for the demo that's fine.
# ---------------------------------------------------------------------


def main() -> int:
    # Force line-buffering so a client sees our responses immediately
    # even if the OS would buffer otherwise.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            # JSON-RPC parse error — return a response without an id
            # since we can't extract one from a malformed message.
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"parse error: {exc}"},
            }) + "\n")
            sys.stdout.flush()
            continue

        response = dispatch(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
