"""
MCP (Model Context Protocol) client for the Olympus agentlib.

W9-10 feature: let users hand Olympus a third-party MCP server and
have its tools show up gated, audited, and rolled-back like any
native agent tool. The deal is "you wrote the server, we make it
safe."

What's in this module:

  - ``MCPServerConfig`` — declarative description of how to connect
    (stdio command + args; HTTP/SSE deferred to a later pass).
  - ``Transport`` Protocol — the abstract wire. Two implementations
    ship: ``StdioTransport`` (real subprocess) and ``MockTransport``
    (callable that pretends to be a server, for tests).
  - ``MCPClient`` — speaks JSON-RPC 2.0 against the protocol:
    ``initialize`` handshake → ``tools/list`` → ``tools/call``.
  - ``to_langchain_tool`` — adapts one MCP tool descriptor into a
    ``langchain_core.tools.StructuredTool`` so it slots into
    ``AgentSpec.tools`` and gets ``gate_tools`` wrapping for free.
  - ``register_mcp_tools`` — convenience that connects, lists,
    wraps, and appends to an agent's ``tools`` list.

The destructive allowlist is **per-server, supplied by the
integrator** rather than declared by the MCP tool author. The runtime
won't trust a tool's self-declaration that it's safe — if a tool
should re-prompt before firing, the integrator names it in
``destructive`` at registration time.

What's intentionally out of scope for the first pass:
  - HTTP and SSE transports (stdio + JSON-RPC over line-delimited
    stdin/stdout is the protocol baseline; the other transports
    layer on top of the same JSON-RPC envelope).
  - Server-initiated requests / sampling / notifications beyond
    the post-initialize ``initialized`` notification.
  - Resources (``resources/list``, ``resources/read``) — tools are
    the immediate-value primitive; resources can come next.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence

from langchain_core.tools import StructuredTool

from .spec import AgentSpec

logger = logging.getLogger(__name__)


# Protocol baseline. Spec versions tick forward fast — we report a
# version we know how to talk and let the server negotiate.
PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "olympus-agentlib", "version": "0.1.0"}


@dataclass
class MCPServerConfig:
    """How to reach an MCP server.

    Two transport modes — pick exactly one:

    1. **stdio** (subprocess): set ``command`` + ``args`` (+ optional
       ``env``, ``cwd``). E.g.
       ``command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]``.
       Environment variables are merged into the subprocess's env.

    2. **HTTP** (remote): set ``url`` (+ optional ``headers``). The
       URL is the MCP server's Streamable-HTTP endpoint; ``headers``
       commonly carries an ``Authorization`` bearer token.

    ``name`` is a human-readable label that ends up on every tool
    Olympus registers from this server (so a "read" tool from
    server "github" becomes "github_read").
    """

    name: str
    # stdio transport
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    # http transport
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # Per-server allowlist of tool names that must route through
    # ApprovalHook. The MCP spec doesn't carry a destructive flag,
    # so the integrator supplies one. Names are matched against the
    # *un-prefixed* tool name returned by the server.
    destructive: set[str] = field(default_factory=set)


class MCPError(RuntimeError):
    """A failure from the MCP server side — either an error envelope
    in a JSON-RPC response or an isError tool result."""


class Transport(Protocol):
    """Bidirectional JSON-RPC pipe. Send a request dict, get a
    response dict back. ``close()`` releases resources."""

    def send(self, message: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]: ...
    def notify(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
    def close(self) -> None: ...


class MockTransport:
    """In-process transport for tests.

    Construct with a callable ``handler(message) -> response`` that
    plays the role of an MCP server. The handler can raise to simulate
    a wire error.

    For notifications, the handler is called but its return value is
    ignored. Threading isn't necessary because the test-side is fully
    synchronous."""

    def __init__(self, handler: Callable[[dict[str, Any]], dict[str, Any]]):
        self.handler = handler
        self.sent: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self._closed = False

    def send(self, message: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        if self._closed:
            raise MCPError("transport closed")
        self.sent.append(dict(message))
        return self.handler(dict(message))

    def notify(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise MCPError("transport closed")
        self.notifications.append(dict(message))

    def close(self) -> None:
        self._closed = True


class StdioTransport:
    """Subprocess-based JSON-RPC over line-delimited stdin/stdout.

    Each request is one JSON object per line, response same. Standard
    error is forwarded to the host's stderr-equivalent (logger.warning)
    so a misbehaving server is debuggable without swallowing output.

    Thread-safe under the simple "one request at a time" model used by
    ``MCPClient`` (request/response IDs are monotonically allocated by
    the client; we don't multiplex)."""

    def __init__(self, config: MCPServerConfig):
        if not config.command:
            raise ValueError("MCPServerConfig.command is required for StdioTransport")
        env = dict(os.environ)
        env.update(config.env)
        self._proc = subprocess.Popen(
            [config.command, *config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            env=env,
            cwd=config.cwd,
        )
        self._lock = threading.Lock()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True,
            name=f"mcp-stderr:{config.name}",
        )
        self._stderr_thread.start()
        self._config_name = config.name

    def _drain_stderr(self) -> None:
        stderr = self._proc.stderr
        if stderr is None:
            return
        for line in iter(stderr.readline, ""):
            if line.strip():
                logger.warning("mcp[%s] stderr: %s", self._config_name, line.rstrip())

    def send(self, message: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        with self._lock:
            self._write(message)
            return self._read(timeout)

    def notify(self, message: dict[str, Any]) -> None:
        with self._lock:
            self._write(message)

    def _write(self, message: dict[str, Any]) -> None:
        if self._proc.stdin is None or self._proc.stdin.closed:
            raise MCPError("MCP server stdin closed")
        line = json.dumps(message) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def _read(self, timeout: float) -> dict[str, Any]:
        if self._proc.stdout is None:
            raise MCPError("MCP server stdout missing")
        deadline = time.monotonic() + timeout
        # readline is blocking — schedule a watchdog thread that
        # terminates the process if the deadline passes.
        watchdog = threading.Timer(timeout, self._watchdog_kill)
        watchdog.start()
        try:
            line = self._proc.stdout.readline()
        finally:
            watchdog.cancel()
        if not line:
            raise MCPError(
                f"MCP server {self._config_name!r} produced no response "
                f"(possibly crashed; check stderr)"
            )
        if time.monotonic() > deadline:
            raise MCPError(
                f"MCP server {self._config_name!r} exceeded {timeout:.0f}s"
            )
        return json.loads(line)

    def _watchdog_kill(self) -> None:
        logger.warning(
            "mcp[%s] watchdog: response deadline exceeded, terminating",
            self._config_name,
        )
        try:
            self._proc.terminate()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
        except Exception:
            pass


class HttpTransport:
    """JSON-RPC over HTTP (Streamable HTTP variant of the MCP spec).

    One POST per request. Response body is either ``application/json``
    (the common case) or ``text/event-stream`` (the server is going
    to keep pushing notifications). For tools we only need the first
    event, which the spec mandates be the request's response.

    Sessions: the server may issue an ``Mcp-Session-Id`` header on
    the response to ``initialize``. We capture it and echo it on
    every subsequent request — that's how servers correlate ongoing
    state with this client.

    Notifications (no id, no response expected by the spec) are
    POSTed with a short timeout and the response body discarded.

    Stdlib-only (urllib.request). No new deps."""

    def __init__(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 30.0,
    ):
        if not url:
            raise ValueError("HttpTransport requires a non-empty url")
        self.url = url
        # Build default headers but let caller override.
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            # The MCP HTTP spec wants the client to advertise both —
            # the server picks based on whether it wants to stream.
            "Accept": "application/json, text/event-stream",
        }
        self._headers.update(headers or {})
        self._timeout = timeout
        self._session_id: Optional[str] = None
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        body = json.dumps(message).encode("utf-8")
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(
            self.url, data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # Capture session id from any response that carries one
                # (commonly the initialize response, but spec allows
                # the server to issue/rotate at any time).
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    with self._lock:
                        self._session_id = sid
                content_type = (resp.headers.get("Content-Type") or "").lower()
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise MCPError(
                f"MCP HTTP {self.url!r} returned {exc.code} {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise MCPError(
                f"MCP HTTP {self.url!r} unreachable: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise MCPError(
                f"MCP HTTP {self.url!r} transport failure: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if "text/event-stream" in content_type:
            return _parse_sse_first_message(raw.decode("utf-8"))
        # application/json (or anything else we treat as JSON).
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MCPError(
                f"MCP HTTP {self.url!r} returned non-JSON body "
                f"(content-type={content_type!r}): {exc}"
            ) from exc

    def notify(self, message: dict[str, Any]) -> None:
        # Fire-and-forget. Spec says notifications get a 202 with no
        # body; we don't care either way.
        try:
            self.send(message, timeout=5.0)
        except MCPError:
            # Notifications are best-effort. A failure here would
            # spam the logs; the next real request will surface the
            # connectivity issue.
            pass

    def close(self) -> None:
        # No persistent connection state to release (urllib's
        # default opener handles keep-alive transparently). The
        # session id stays meaningful for a short window after
        # close in case the caller re-opens; clearing it would be
        # harmless but also pointless.
        return None


def _parse_sse_first_message(text: str) -> dict[str, Any]:
    """Extract the first JSON-RPC message from an SSE body.

    SSE events are blank-line-separated; each event has one or more
    ``field: value`` lines. The MCP spec only emits ``data:`` lines
    carrying the JSON. We accumulate the data lines for the first
    event and parse them as one JSON object."""
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
            continue
        if line.strip() == "" and data_lines:
            break  # end of first event
    if not data_lines:
        raise MCPError("SSE body had no data lines")
    try:
        return json.loads("\n".join(data_lines))
    except json.JSONDecodeError as exc:
        raise MCPError(f"SSE first event JSON parse: {exc}") from exc


def build_transport(config: MCPServerConfig) -> Transport:
    """Pick a transport implementation based on the config.

    Rules of precedence:
      - ``url`` set       → HttpTransport
      - ``command`` set   → StdioTransport
      - neither           → ValueError
      - both              → ValueError (ambiguous; pick one)"""
    has_url = bool(config.url)
    has_cmd = bool(config.command)
    if has_url and has_cmd:
        raise ValueError(
            f"MCPServerConfig {config.name!r} has both url and command — "
            "pick one transport"
        )
    if has_url:
        return HttpTransport(config.url, headers=config.headers)
    if has_cmd:
        return StdioTransport(config)
    raise ValueError(
        f"MCPServerConfig {config.name!r} has neither url nor command — "
        "transport is undefined"
    )


class MCPClient:
    """High-level client over a ``Transport``.

    Lifecycle:
      1. Construct with a Transport.
      2. ``initialize()`` — handshake. Returns the server's capability
         block (kept on the client for inspection).
      3. ``list_tools()`` — returns the catalog.
      4. ``call_tool(name, args)`` — invoke. Returns a parsed
         ``MCPToolResult`` (text + isError flag).
      5. ``close()`` — tears down the transport.

    Each operation allocates a monotonically increasing request id;
    we don't multiplex, so this stays simple."""

    def __init__(self, transport: Transport):
        self.transport = transport
        self._next_id = 1
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self._initialized = False

    def initialize(self, timeout: float = 10.0) -> dict[str, Any]:
        """Send the spec-mandated ``initialize`` request, then the
        ``initialized`` notification. Returns the server's
        capabilities block."""
        resp = self._send_request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }, timeout=timeout)
        self.server_info = resp.get("serverInfo", {})
        self.capabilities = resp.get("capabilities", {})
        # Post-handshake notification — the spec requires it.
        self.transport.notify({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        self._initialized = True
        return self.capabilities

    def list_tools(self, timeout: float = 10.0) -> list[dict[str, Any]]:
        """Return the server's tool catalog as a list of MCP tool
        descriptors. Each descriptor: ``{name, description, inputSchema}``."""
        self._require_initialized()
        resp = self._send_request("tools/list", {}, timeout=timeout)
        tools = resp.get("tools", [])
        if not isinstance(tools, list):
            raise MCPError(f"tools/list returned non-list: {tools!r}")
        return tools

    def call_tool(
        self, name: str, arguments: dict[str, Any], timeout: float = 30.0,
    ) -> "MCPToolResult":
        """Invoke a tool. Returns the parsed result. A server-side
        error (isError=true OR a JSON-RPC error envelope) is surfaced
        as an ``MCPError``."""
        self._require_initialized()
        resp = self._send_request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        is_error = bool(resp.get("isError", False))
        content = resp.get("content", [])
        text = _join_content_text(content)
        return MCPToolResult(text=text, is_error=is_error, raw=resp)

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception:
            pass

    def _send_request(
        self, method: str, params: dict[str, Any], *, timeout: float,
    ) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        request = {
            "jsonrpc": "2.0", "id": msg_id, "method": method,
            "params": params,
        }
        response = self.transport.send(request, timeout=timeout)
        if "error" in response:
            err = response["error"]
            code = err.get("code", -1) if isinstance(err, dict) else -1
            message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise MCPError(f"MCP method {method!r} failed: [{code}] {message}")
        return response.get("result", {})

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise MCPError(
                "MCPClient: call .initialize() before tool ops"
            )


@dataclass
class MCPToolResult:
    """Parsed tool-call result. ``text`` is the concatenated content
    blocks (one text block per item) so the wrapping langchain tool
    has a single string to return."""

    text: str
    is_error: bool
    raw: dict[str, Any]


def _join_content_text(content: Any) -> str:
    """The MCP spec says content is a list of blocks, each typed
    (``text`` / ``image`` / ``resource``). For v1 we only surface
    text blocks; non-text blocks are summarised as ``[<type> block]``."""
    if not isinstance(content, list):
        if isinstance(content, str):
            return content
        return str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        btype = block.get("type", "text")
        if btype == "text":
            parts.append(str(block.get("text", "")))
        else:
            parts.append(f"[{btype} block]")
    return "\n".join(parts)


def to_langchain_tool(
    mcp_tool: dict[str, Any],
    client: MCPClient,
    *,
    name_prefix: str = "",
) -> StructuredTool:
    """Adapt one MCP tool descriptor into a ``StructuredTool``.

    The wrapped invoke() calls ``client.call_tool`` synchronously and
    returns the joined text content. ``isError=true`` results are
    surfaced as the string ``"MCP_ERROR: <text>"`` so an agent's LLM
    sees the failure rather than the runtime raising — the gating
    machinery downstream uses the return value, not exceptions.

    ``name_prefix`` is prepended (with an underscore) to the tool
    name so multiple servers can declare tools called ``read``
    without collisions in the agent's tool list."""
    tool_name = mcp_tool["name"]
    full_name = f"{name_prefix}_{tool_name}" if name_prefix else tool_name
    description = mcp_tool.get("description", f"MCP tool {tool_name}")
    schema = mcp_tool.get("inputSchema") or {"type": "object", "properties": {}}

    def _invoke(**kwargs: Any) -> str:
        result = client.call_tool(tool_name, kwargs)
        if result.is_error:
            return f"MCP_ERROR: {result.text}"
        return result.text

    return StructuredTool.from_function(
        func=_invoke,
        name=full_name,
        description=description,
        args_schema=schema,
    )


def register_mcp_tools(
    spec: AgentSpec,
    config: MCPServerConfig,
    *,
    client: Optional[MCPClient] = None,
    name_prefix: Optional[str] = None,
) -> list[StructuredTool]:
    """Connect to an MCP server, list its tools, wrap each as a
    StructuredTool, and append them to ``spec.tools``. Names listed in
    ``config.destructive`` (matched against the *un-prefixed* tool
    name) are added to ``spec.destructive_verbs`` — using the
    prefixed name, since that's what the runtime sees.

    The integrator can pre-construct the client (e.g. for tests) by
    passing ``client=``; otherwise this function builds one from a
    ``StdioTransport`` and runs ``initialize()`` itself.

    Returns the list of added langchain tools (also already appended
    to ``spec.tools``)."""
    prefix = name_prefix if name_prefix is not None else config.name
    owned_client = False
    if client is None:
        client = MCPClient(build_transport(config))
        client.initialize()
        owned_client = True

    try:
        descriptors = client.list_tools()
    except Exception:
        if owned_client:
            client.close()
        raise

    new_tools = [
        to_langchain_tool(d, client, name_prefix=prefix)
        for d in descriptors
    ]

    existing = list(spec.tools) if spec.tools else []
    spec.tools = existing + new_tools

    if config.destructive:
        full_destructive = {
            f"{prefix}_{t}" if prefix else t for t in config.destructive
        }
        spec.destructive_verbs = set(spec.destructive_verbs) | full_destructive

    return new_tools


def parse_command_string(command: str) -> tuple[str, list[str]]:
    """Convenience for users who'd rather hand a single CLI string.

    ``parse_command_string("npx -y @scope/server-fs /tmp")`` →
    ``("npx", ["-y", "@scope/server-fs", "/tmp"])``."""
    parts = shlex.split(command)
    if not parts:
        raise ValueError("empty command")
    return parts[0], parts[1:]


__all__ = [
    "MCPServerConfig",
    "MCPClient",
    "MCPError",
    "MCPToolResult",
    "Transport",
    "StdioTransport",
    "HttpTransport",
    "MockTransport",
    "build_transport",
    "to_langchain_tool",
    "register_mcp_tools",
    "parse_command_string",
    "PROTOCOL_VERSION",
    "CLIENT_INFO",
]


# Suppress unused-Sequence warnings when type-checking is enabled
_ = Sequence  # type: ignore[has-type]
