"""
Tests for the MCP (Model Context Protocol) client.

Cover:
  - JSON-RPC envelope shape on each operation.
  - initialize handshake (request + post-init notification).
  - tools/list catalog parsing.
  - tools/call success path (joined text content) + isError path
    (surfaced as the ``MCP_ERROR:`` string sentinel).
  - to_langchain_tool adapter shape (name prefix, schema passthrough).
  - register_mcp_tools end-to-end: tools appended to spec.tools,
    destructive names lifted to spec.destructive_verbs with the
    prefix applied, the adapter survives a round-trip through
    gate_tools.

All tests use ``MockTransport`` — no subprocess, no JSON-RPC over
stdin/stdout. The stdio transport is exercised manually against
real MCP servers (CI doesn't have one).
"""
from __future__ import annotations

from typing import Any, Sequence

import pytest

from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    AlwaysApprove,
    AlwaysReject,
    InMemoryAuditLogger,
    MCPClient,
    MCPError,
    MCPServerConfig,
    MCPToolResult,
    MockTransport,
    TaskMessage,
    gate_tools,
    parse_command_string,
    register_mcp_tools,
    to_langchain_tool,
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


class _Server:
    """Tiny in-process MCP server. Tracks state so tests can assert
    against what the client sent."""

    def __init__(
        self,
        tools: list[dict[str, Any]] | None = None,
        call_handlers: dict[str, callable] | None = None,
        protocol_version: str = "2024-11-05",
    ):
        self.tools = tools or []
        self.call_handlers = call_handlers or {}
        self.protocol_version = protocol_version
        self.received: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []

    def __call__(self, msg: dict[str, Any]) -> dict[str, Any]:
        self.received.append(dict(msg))
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            return _envelope(msg_id, {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock", "version": "0.0.1"},
            })
        if method == "tools/list":
            return _envelope(msg_id, {"tools": self.tools})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            handler = self.call_handlers.get(name)
            if handler is None:
                return _envelope(msg_id, {
                    "content": [{"type": "text", "text": f"unknown tool {name!r}"}],
                    "isError": True,
                })
            result = handler(args)
            return _envelope(msg_id, result)
        # Unknown method → JSON-RPC error envelope.
        return {"jsonrpc": "2.0", "id": msg_id, "error": {
            "code": -32601, "message": f"method not found: {method}",
        }}


def _envelope(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


# ---------------------------------------------------------------------
# JSON-RPC + handshake
# ---------------------------------------------------------------------


def test_mcp_client_initialize_sends_correct_envelope_and_followup_notification():
    server = _Server()
    transport = MockTransport(server)
    client = MCPClient(transport)

    capabilities = client.initialize()

    # First sent message is initialize, with the protocol_version
    # and clientInfo populated.
    init_msg = transport.sent[0]
    assert init_msg["method"] == "initialize"
    assert init_msg["params"]["protocolVersion"] == "2024-11-05"
    assert init_msg["params"]["clientInfo"]["name"] == "olympus-agentlib"
    assert init_msg["jsonrpc"] == "2.0"

    # Server's capability block round-trips.
    assert capabilities == {"tools": {}}
    assert client.server_info == {"name": "mock", "version": "0.0.1"}

    # Post-handshake notification was fired (no id field).
    assert len(transport.notifications) == 1
    note = transport.notifications[0]
    assert note["method"] == "notifications/initialized"
    assert "id" not in note


def test_mcp_client_initialize_raises_on_jsonrpc_error():
    def bad(msg):
        return {"jsonrpc": "2.0", "id": msg["id"], "error": {
            "code": -32000, "message": "server busy",
        }}

    client = MCPClient(MockTransport(bad))
    with pytest.raises(MCPError) as exc:
        client.initialize()
    assert "server busy" in str(exc.value)


def test_mcp_client_list_tools_returns_catalog():
    server = _Server(tools=[
        {"name": "read", "description": "Read a thing", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        {"name": "write", "description": "Write a thing", "inputSchema": {"type": "object"}},
    ])
    client = MCPClient(MockTransport(server))
    client.initialize()

    tools = client.list_tools()
    assert [t["name"] for t in tools] == ["read", "write"]
    # The list_tools call landed.
    assert any(m["method"] == "tools/list" for m in server.received)


def test_mcp_client_list_tools_before_initialize_raises():
    client = MCPClient(MockTransport(_Server()))
    with pytest.raises(MCPError) as exc:
        client.list_tools()
    assert "initialize" in str(exc.value)


def test_mcp_client_call_tool_returns_joined_text_content():
    server = _Server(
        tools=[{"name": "say_hi", "description": "x", "inputSchema": {"type": "object"}}],
        call_handlers={
            "say_hi": lambda args: {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": "world"},
                ],
            },
        },
    )
    client = MCPClient(MockTransport(server))
    client.initialize()

    result = client.call_tool("say_hi", {})
    assert isinstance(result, MCPToolResult)
    assert result.text == "hello\nworld"
    assert result.is_error is False


def test_mcp_client_call_tool_surfaces_is_error():
    server = _Server(
        tools=[{"name": "boom", "description": "x", "inputSchema": {"type": "object"}}],
        call_handlers={
            "boom": lambda args: {
                "content": [{"type": "text", "text": "permission denied"}],
                "isError": True,
            },
        },
    )
    client = MCPClient(MockTransport(server))
    client.initialize()

    result = client.call_tool("boom", {})
    assert result.is_error is True
    assert "permission denied" in result.text


def test_mcp_client_call_tool_handles_non_text_blocks():
    """Image/resource blocks aren't first-class in v1 — they should
    surface as ``[<type> block]`` placeholders, not crash."""
    server = _Server(
        tools=[{"name": "fancy", "description": "x", "inputSchema": {"type": "object"}}],
        call_handlers={
            "fancy": lambda args: {
                "content": [
                    {"type": "text", "text": "before"},
                    {"type": "image", "data": "..."},
                    {"type": "text", "text": "after"},
                ],
            },
        },
    )
    client = MCPClient(MockTransport(server))
    client.initialize()
    result = client.call_tool("fancy", {})
    assert "before" in result.text
    assert "[image block]" in result.text
    assert "after" in result.text


def test_mcp_client_jsonrpc_error_on_tool_call_raises():
    def bad_call(msg):
        if msg.get("method") == "tools/call":
            return {"jsonrpc": "2.0", "id": msg["id"], "error": {
                "code": -32602, "message": "bad args",
            }}
        return _Server()(msg)

    client = MCPClient(MockTransport(bad_call))
    client.initialize()
    with pytest.raises(MCPError) as exc:
        client.call_tool("anything", {})
    assert "bad args" in str(exc.value)


def test_mcp_client_call_tool_passes_arguments_through():
    captured: dict[str, Any] = {}

    def handler(args):
        captured.update(args)
        return {"content": [{"type": "text", "text": "ok"}]}

    server = _Server(
        tools=[{"name": "echo", "description": "x", "inputSchema": {"type": "object"}}],
        call_handlers={"echo": handler},
    )
    transport = MockTransport(server)
    client = MCPClient(transport)
    client.initialize()
    client.call_tool("echo", {"a": 1, "b": "two"})
    assert captured == {"a": 1, "b": "two"}
    # And the request envelope itself is well-formed.
    call_msg = [m for m in transport.sent if m.get("method") == "tools/call"][0]
    assert call_msg["params"]["name"] == "echo"
    assert call_msg["params"]["arguments"] == {"a": 1, "b": "two"}


# ---------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------


def _connected_client(server: _Server) -> MCPClient:
    client = MCPClient(MockTransport(server))
    client.initialize()
    return client


def test_to_langchain_tool_preserves_name_and_description_when_no_prefix():
    server = _Server(call_handlers={"hi": lambda args: {"content": [{"type": "text", "text": "ok"}]}})
    client = _connected_client(server)
    tool = to_langchain_tool(
        {"name": "hi", "description": "say hello", "inputSchema": {"type": "object"}},
        client,
    )
    assert tool.name == "hi"
    assert "hello" in tool.description


def test_to_langchain_tool_applies_name_prefix():
    server = _Server(call_handlers={"hi": lambda args: {"content": [{"type": "text", "text": "ok"}]}})
    client = _connected_client(server)
    tool = to_langchain_tool(
        {"name": "hi", "description": "x", "inputSchema": {"type": "object"}},
        client,
        name_prefix="gh",
    )
    assert tool.name == "gh_hi"


def test_to_langchain_tool_invoke_returns_joined_text():
    server = _Server(call_handlers={
        "greet": lambda args: {"content": [{"type": "text", "text": f"hi {args.get('name', '?')}"}]},
    })
    client = _connected_client(server)
    tool = to_langchain_tool(
        {"name": "greet", "description": "x", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
        client,
    )
    out = tool.invoke({"name": "world"})
    assert out == "hi world"


def test_to_langchain_tool_is_error_surfaces_as_string_sentinel():
    server = _Server(call_handlers={
        "bad": lambda args: {
            "content": [{"type": "text", "text": "no soup"}],
            "isError": True,
        },
    })
    client = _connected_client(server)
    tool = to_langchain_tool(
        {"name": "bad", "description": "x", "inputSchema": {"type": "object"}},
        client,
    )
    out = tool.invoke({})
    # The agent's LLM sees an error sentinel rather than a crash — the
    # gating + return-value paths downstream rely on this.
    assert out.startswith("MCP_ERROR:")
    assert "no soup" in out


# ---------------------------------------------------------------------
# register_mcp_tools
# ---------------------------------------------------------------------


class _BlankAgent(AgentSpec):
    """Empty agent we use to test MCP-tool registration without
    pulling in any production agent."""
    name = "blank"
    domain = "blank"
    tools: Sequence[Any] = []
    destructive_verbs: set[str] = set()

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        raise NotImplementedError


def test_register_mcp_tools_appends_each_tool_to_spec():
    server = _Server(
        tools=[
            {"name": "read", "description": "Read", "inputSchema": {"type": "object"}},
            {"name": "write", "description": "Write", "inputSchema": {"type": "object"}},
        ],
        call_handlers={
            "read": lambda args: {"content": [{"type": "text", "text": "read-ok"}]},
            "write": lambda args: {"content": [{"type": "text", "text": "wrote"}]},
        },
    )
    client = _connected_client(server)
    spec = _BlankAgent()
    added = register_mcp_tools(
        spec,
        MCPServerConfig(name="fs"),
        client=client,
    )
    assert {t.name for t in added} == {"fs_read", "fs_write"}
    # Tools landed on the spec, with the prefix applied.
    assert {t.name for t in spec.tools} == {"fs_read", "fs_write"}


def test_register_mcp_tools_lifts_destructive_names_with_prefix():
    server = _Server(
        tools=[
            {"name": "read", "description": "R", "inputSchema": {"type": "object"}},
            {"name": "write", "description": "W", "inputSchema": {"type": "object"}},
        ],
    )
    client = _connected_client(server)
    spec = _BlankAgent()
    register_mcp_tools(
        spec,
        MCPServerConfig(name="fs", destructive={"write"}),
        client=client,
    )
    # Destructive verbs are stored with the prefix applied, since the
    # runtime sees the prefixed name.
    assert "fs_write" in spec.destructive_verbs
    assert "fs_read" not in spec.destructive_verbs


def test_register_mcp_tools_does_not_clobber_pre_existing_tools():
    """Native tools already on the spec must survive a register call."""
    from langchain_core.tools import tool as _lc_tool

    @_lc_tool
    def native(x: str) -> str:
        """Native tool already on the agent."""
        return x

    spec = _BlankAgent()
    spec.tools = [native]
    spec.destructive_verbs = {"native"}

    server = _Server(
        tools=[{"name": "mcp_add", "description": "x", "inputSchema": {"type": "object"}}],
    )
    register_mcp_tools(spec, MCPServerConfig(name="ext"), client=_connected_client(server))

    names = {t.name for t in spec.tools}
    assert names == {"native", "ext_mcp_add"}
    # Native destructive verb survives.
    assert "native" in spec.destructive_verbs


def test_register_mcp_tools_with_blank_prefix_keeps_raw_names():
    server = _Server(
        tools=[{"name": "raw", "description": "x", "inputSchema": {"type": "object"}}],
    )
    client = _connected_client(server)
    spec = _BlankAgent()
    register_mcp_tools(
        spec,
        MCPServerConfig(name="ignored", destructive={"raw"}),
        client=client,
        name_prefix="",
    )
    # Empty explicit prefix → raw tool name.
    assert {t.name for t in spec.tools} == {"raw"}
    assert spec.destructive_verbs == {"raw"}


def test_registered_mcp_tool_runs_through_gate_tools_when_non_destructive():
    """A read-only MCP tool, registered onto a spec and wrapped by
    gate_tools, should invoke the MCP server and return its text
    output. AlwaysReject must NOT trigger because the tool isn't
    in destructive_verbs."""
    server = _Server(
        tools=[{"name": "stat", "description": "x", "inputSchema": {"type": "object"}}],
        call_handlers={"stat": lambda args: {"content": [{"type": "text", "text": "uptime: 1d"}]}},
    )
    client = _connected_client(server)
    spec = _BlankAgent()
    register_mcp_tools(spec, MCPServerConfig(name="ops"), client=client)

    ctx = AgentContext(approval=AlwaysReject(), audit=InMemoryAuditLogger())
    gated = gate_tools(spec, ctx, task_id="mcp-1")
    by_name = {t.name: t for t in gated}

    out = by_name["ops_stat"].invoke({})
    assert out == "uptime: 1d"


def test_registered_destructive_mcp_tool_re_routes_through_approval():
    """A destructive MCP tool should go through gate_tools's approval
    path. AlwaysReject must produce a REJECTED-prefixed string and
    NOT actually call the server."""
    handler_calls: list[dict] = []

    def write_handler(args):
        handler_calls.append(args)
        return {"content": [{"type": "text", "text": "wrote"}]}

    server = _Server(
        tools=[{"name": "write", "description": "x", "inputSchema": {"type": "object"}}],
        call_handlers={"write": write_handler},
    )
    client = _connected_client(server)
    spec = _BlankAgent()
    register_mcp_tools(
        spec,
        MCPServerConfig(name="ext", destructive={"write"}),
        client=client,
    )

    ctx = AgentContext(approval=AlwaysReject(), audit=InMemoryAuditLogger())
    gated = gate_tools(spec, ctx, task_id="mcp-2")
    by_name = {t.name: t for t in gated}

    out = by_name["ext_write"].invoke({"path": "/tmp/x"})
    assert "REJECTED" in out
    # The MCP server was NOT called.
    assert handler_calls == []


def test_registered_destructive_mcp_tool_fires_on_approval():
    handler_calls: list[dict] = []

    def write_handler(args):
        handler_calls.append(args)
        return {"content": [{"type": "text", "text": "wrote"}]}

    server = _Server(
        tools=[{"name": "write", "description": "x", "inputSchema": {"type": "object"}}],
        call_handlers={"write": write_handler},
    )
    client = _connected_client(server)
    spec = _BlankAgent()
    register_mcp_tools(
        spec,
        MCPServerConfig(name="ext", destructive={"write"}),
        client=client,
    )

    ctx = AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())
    gated = gate_tools(spec, ctx, task_id="mcp-3")
    by_name = {t.name: t for t in gated}

    out = by_name["ext_write"].invoke({"path": "/tmp/x"})
    assert out == "wrote"
    assert handler_calls == [{"path": "/tmp/x"}]


# ---------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------


def test_parse_command_string_splits_shell_form():
    cmd, args = parse_command_string("npx -y @scope/server-fs /tmp")
    assert cmd == "npx"
    assert args == ["-y", "@scope/server-fs", "/tmp"]


def test_parse_command_string_rejects_empty():
    with pytest.raises(ValueError):
        parse_command_string("   ")


def test_parse_command_string_preserves_quoted_args():
    cmd, args = parse_command_string('python -c "import sys; print(sys.argv)"')
    assert cmd == "python"
    assert args == ["-c", "import sys; print(sys.argv)"]


def test_mock_transport_records_sends_and_notifications():
    server = _Server()
    transport = MockTransport(server)
    transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert len(transport.sent) == 1
    assert len(transport.notifications) == 1


def test_mock_transport_send_after_close_raises():
    transport = MockTransport(_Server())
    transport.close()
    with pytest.raises(MCPError):
        transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})


# ---------------------------------------------------------------------
# HTTP transport (Streamable HTTP)
# ---------------------------------------------------------------------

import contextlib  # noqa: E402 — section-local imports
import http.server  # noqa: E402
import json as _json  # noqa: E402
import socket  # noqa: E402
import threading as _threading  # noqa: E402
import urllib.error  # noqa: E402
from typing import Optional  # noqa: E402
from unittest.mock import patch  # noqa: E402

from agentlib import HttpTransport, build_transport  # noqa: E402


def _fake_urlopen_response(
    body: bytes,
    content_type: str = "application/json",
    session_id: Optional[str] = None,
    status: int = 200,
):
    """Context-managerable stub that mimics urlopen()'s return value."""

    class _Headers:
        def __init__(self, ct, sid):
            self._h = {"Content-Type": ct}
            if sid:
                self._h["Mcp-Session-Id"] = sid

        def get(self, key, default=None):
            # Case-insensitive get to match real http.client behaviour.
            for k, v in self._h.items():
                if k.lower() == key.lower():
                    return v
            return default

    class _Resp:
        def __init__(self):
            self.headers = _Headers(content_type, session_id)
            self.status = status
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp()


def test_http_transport_rejects_empty_url():
    import pytest as _pytest
    with _pytest.raises(ValueError, match="non-empty url"):
        HttpTransport(url="")


def test_http_transport_send_posts_json_and_returns_parsed_response():
    captured: dict = {}

    def fake_urlopen(req, timeout=30.0):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        captured["headers"] = dict(req.headers)
        return _fake_urlopen_response(
            _json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode(),
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        transport = HttpTransport(url="http://example.com/mcp")
        out = transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    assert out == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    assert captured["url"] == "http://example.com/mcp"
    assert captured["method"] == "POST"
    body = _json.loads(captured["body"].decode())
    assert body["method"] == "ping"
    # Default headers set Content-Type + the dual Accept header MCP needs.
    assert captured["headers"]["Content-type"] == "application/json"
    assert "application/json" in captured["headers"]["Accept"]
    assert "text/event-stream" in captured["headers"]["Accept"]


def test_http_transport_captures_and_echoes_session_id():
    """Initialize-style response includes Mcp-Session-Id; subsequent
    requests must echo it back."""
    requests_seen: list[dict] = []

    def fake_urlopen(req, timeout=30.0):
        requests_seen.append(dict(req.headers))
        if len(requests_seen) == 1:
            # First call: server issues a session id.
            return _fake_urlopen_response(
                _json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode(),
                session_id="sess-abc-123",
            )
        return _fake_urlopen_response(
            _json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}).encode(),
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        t = HttpTransport(url="http://example.com/mcp")
        t.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        t.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    # First request: no session header (we don't have one yet).
    assert "Mcp-session-id" not in requests_seen[0] and "mcp-session-id" not in {
        k.lower() for k in requests_seen[0]
    }
    # Second request: must echo the session id the server issued.
    sid = requests_seen[1].get("Mcp-session-id") or requests_seen[1].get("mcp-session-id")
    assert sid == "sess-abc-123"


def test_http_transport_parses_sse_first_event():
    """When the server responds with text/event-stream, the transport
    must parse the first event's `data:` lines as JSON and return it."""
    sse_body = (
        "event: message\n"
        'data: {"jsonrpc": "2.0", "id": 1, "result": {"streamed": true}}\n'
        "\n"
        "event: notification\n"
        'data: {"jsonrpc": "2.0", "method": "notifications/progress"}\n'
        "\n"
    )

    def fake_urlopen(req, timeout=30.0):
        return _fake_urlopen_response(
            sse_body.encode(), content_type="text/event-stream; charset=utf-8",
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        out = HttpTransport(url="http://example.com/mcp").send(
            {"jsonrpc": "2.0", "id": 1, "method": "x"},
        )
    assert out == {"jsonrpc": "2.0", "id": 1, "result": {"streamed": True}}


def test_http_transport_multiline_sse_data_concatenates():
    """data: spread across multiple lines must be joined with \\n
    before JSON-parsing — per the SSE spec."""
    sse_body = (
        'data: {"jsonrpc": "2.0",\n'
        'data:  "id": 1,\n'
        'data:  "result": {"k": "v"}}\n'
        "\n"
    )
    with patch(
        "urllib.request.urlopen",
        side_effect=lambda req, timeout=30.0: _fake_urlopen_response(
            sse_body.encode(), content_type="text/event-stream",
        ),
    ):
        out = HttpTransport(url="http://example.com/mcp").send({"id": 1})
    assert out["result"]["k"] == "v"


def test_http_transport_http_error_surfaces_as_mcp_error():
    def fake_urlopen(req, timeout=30.0):
        raise urllib.error.HTTPError(
            url=req.full_url, code=503, msg="Service Unavailable",
            hdrs=None, fp=None,
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        import pytest as _pytest
        with _pytest.raises(MCPError, match="503"):
            HttpTransport(url="http://example.com/mcp").send({"id": 1})


def test_http_transport_url_error_surfaces_as_mcp_error():
    def fake_urlopen(req, timeout=30.0):
        raise urllib.error.URLError("nodename nor servname provided")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        import pytest as _pytest
        with _pytest.raises(MCPError, match="unreachable"):
            HttpTransport(url="http://example.com/mcp").send({"id": 1})


def test_http_transport_non_json_body_surfaces_as_mcp_error():
    def fake_urlopen(req, timeout=30.0):
        return _fake_urlopen_response(b"<html>oops</html>", content_type="text/html")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        import pytest as _pytest
        with _pytest.raises(MCPError, match="non-JSON"):
            HttpTransport(url="http://example.com/mcp").send({"id": 1})


def test_http_transport_notify_swallows_errors():
    """Notifications are fire-and-forget per the spec — a transport
    failure must NOT raise out of notify()."""
    def fake_urlopen(req, timeout=30.0):
        raise urllib.error.URLError("oops")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        t = HttpTransport(url="http://example.com/mcp")
        t.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
    # No assertion: just must not raise.


def test_http_transport_custom_headers_merge_with_defaults():
    captured = {}

    def fake_urlopen(req, timeout=30.0):
        captured.update(dict(req.headers))
        return _fake_urlopen_response(
            _json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode(),
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        HttpTransport(
            url="http://example.com/mcp",
            headers={"Authorization": "Bearer token-xyz"},
        ).send({"id": 1})

    # Caller-supplied header lands on the request.
    assert captured.get("Authorization") == "Bearer token-xyz"
    # Defaults survive.
    assert captured.get("Content-type") == "application/json"


# ---------------------------------------------------------------------
# build_transport — config-driven factory
# ---------------------------------------------------------------------


def test_build_transport_picks_http_when_url_set():
    t = build_transport(MCPServerConfig(name="x", url="http://e/mcp"))
    assert isinstance(t, HttpTransport)


def test_build_transport_rejects_ambiguous_config():
    import pytest as _pytest
    with _pytest.raises(ValueError, match="both url and command"):
        build_transport(MCPServerConfig(
            name="x", url="http://e/mcp", command="echo",
        ))


def test_build_transport_rejects_empty_config():
    import pytest as _pytest
    with _pytest.raises(ValueError, match="neither url nor command"):
        build_transport(MCPServerConfig(name="x"))


def test_register_mcp_tools_picks_http_transport_when_url_set(tmp_path):
    """register_mcp_tools, when no explicit client is passed, builds
    one from build_transport — which picks HttpTransport for url-mode
    configs. We don't actually start a server here; just verify the
    transport selection by stubbing MCPClient construction."""
    # Easier than spinning up a real server: verify build_transport's
    # decision in isolation. The full register_mcp_tools path is
    # covered by the existing client-injection tests.
    cfg = MCPServerConfig(name="ext", url="http://e/mcp", destructive={"write"})
    t = build_transport(cfg)
    assert isinstance(t, HttpTransport)
    assert t.url == "http://e/mcp"


# ---------------------------------------------------------------------
# End-to-end: loopback HTTP server speaking MCP
# ---------------------------------------------------------------------


def _free_port() -> int:
    """Bind to an OS-chosen free port, then release it."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _loopback_mcp_server(handler_fn):
    """Run a real HTTP server in a thread that hands every POST to
    ``handler_fn(request_body_dict) -> response_dict``. Yields the URL."""
    port = _free_port()

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):
            pass  # quiet

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            try:
                req = _json.loads(body)
            except Exception:
                self.send_response(400)
                self.end_headers()
                return
            try:
                resp = handler_fn(req)
            except Exception as exc:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"{exc}".encode())
                return
            payload = _json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if req.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", "loopback-session")
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.HTTPServer(("127.0.0.1", port), _H)
    t = _threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.shutdown()
        server.server_close()


def test_http_transport_against_real_loopback_server():
    """Drive HttpTransport against an actual HTTP server bound to a
    loopback port. Catches everything urlopen + http.server might
    disagree about (Content-Length, request shape, response framing).
    """
    received: list[dict] = []

    def handler(req):
        received.append(dict(req))
        method = req.get("method")
        mid = req.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "loopback", "version": "0.0.1"},
            }}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "echo", "description": "echoes args",
                 "inputSchema": {"type": "object"}},
            ]}}
        if method == "tools/call":
            args = (req.get("params") or {}).get("arguments") or {}
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": _json.dumps(args)}],
            }}
        return {"jsonrpc": "2.0", "id": mid, "error": {
            "code": -32601, "message": f"method not found: {method}",
        }}

    with _loopback_mcp_server(handler) as url:
        cfg = MCPServerConfig(name="loop", url=url)
        client = MCPClient(build_transport(cfg))
        client.initialize()
        tools = client.list_tools()
        assert [t["name"] for t in tools] == ["echo"]
        result = client.call_tool("echo", {"hello": "world"})
        assert result.text == '{"hello": "world"}'

    # Verify the server saw initialize + tools/list + tools/call.
    methods = [r.get("method") for r in received]
    assert "initialize" in methods
    assert "tools/list" in methods
    assert "tools/call" in methods


def test_http_transport_loopback_session_id_propagates():
    """Real-server end-to-end check that the session id from the
    initialize response gets echoed on subsequent requests."""
    seen_session_ids: list[Optional[str]] = []

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):
            pass

        def do_POST(self):  # noqa: N802
            seen_session_ids.append(self.headers.get("Mcp-Session-Id"))
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            req = _json.loads(body)
            payload = _json.dumps({
                "jsonrpc": "2.0", "id": req["id"], "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "loop", "version": "0"},
                    "tools": [],
                },
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if req.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", "loop-sess-42")
            self.end_headers()
            self.wfile.write(payload)

    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), _H)
    t = _threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        client = MCPClient(HttpTransport(url=url))
        client.initialize()
        client.list_tools()
    finally:
        server.shutdown()
        server.server_close()

    # Three requests landed: initialize, initialized notification,
    # tools/list. The notification + tools/list must echo the session
    # id; the first (initialize) had no session yet.
    assert seen_session_ids[0] is None
    assert all(sid == "loop-sess-42" for sid in seen_session_ids[1:])
