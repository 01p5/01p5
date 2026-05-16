# MCP — Model Context Protocol integration

W9-10 headline feature. Olympus accepts a third-party MCP server as a
tool source and makes its tools available to any agent — gated,
audited, and rolled-back the same way native tools are. The deal is
*"you wrote the server, we make it safe."*

Two reference docs to read first:

- The [MCP specification](https://modelcontextprotocol.io/) (revision
  `2024-11-05` is what Olympus currently speaks).
- [`infra/demo-mcp-server/README.md`](../infra/demo-mcp-server/README.md)
  for a tiny working server you can hand to Olympus.

This file is the integration guide on Olympus's side.

## How it fits together

```
                    ┌─────────────────────────┐
                    │  Third-party MCP server │
                    │  (any language, stdio)  │
                    └────────────┬────────────┘
                                 │ JSON-RPC 2.0
                  ┌──────────────▼──────────────┐
                  │ libs/agentlib/mcp.py         │
                  │  ├─ StdioTransport           │
                  │  ├─ MCPClient                │
                  │  └─ register_mcp_tools(...)  │
                  └──────────────┬──────────────┘
                                 │ langchain StructuredTool
                                 ▼
                  ┌─────────────────────────────┐
                  │  AgentSpec.tools             │
                  │  + AgentSpec.destructive_verbs │
                  └──────────────┬──────────────┘
                                 │ gate_tools(...)
                                 ▼
                  ┌─────────────────────────────┐
                  │  ApprovalHook + AuditLogger  │
                  │  + RollbackStore (snapshots) │
                  └─────────────────────────────┘
```

Three things make this safe:

1. **The integrator declares which tools are destructive.** The MCP
   spec doesn't carry a destructive flag — a malicious server can't
   smuggle a `delete_everything` tool in as read-only. You name them
   at registration time via `MCPServerConfig.destructive`.
2. **All MCP tools get name-prefixed by the server.** Two servers can
   both declare a tool called `read` without collision: they land on
   the agent as `serverA_read` and `serverB_read`.
3. **The same gate_tools runtime wraps them.** Once registered they
   look like any other agent tool to the runtime — every call goes
   through the audit log, destructive ones go through ApprovalHook,
   destructive file ops get rollback snapshots when the agent has
   registered a snapshot fn.

## Worked example: the demo server

Step 1 — start the dashboard with the demo server wired:

```python
from agentlib import MCPServerConfig
from dashboard.server import build_default_server

cfg = MCPServerConfig(
    name="demo",
    command="python3",
    args=["infra/demo-mcp-server/server.py"],
    # The notebook is server-mutating state; the integrator (you)
    # decides this counts as destructive.
    destructive={"notes_append"},
)

srv = build_default_server(mcp_servers=[
    {"name": "demo", "target_agent": "programmer", "config": cfg},
])
srv.serve()
```

Step 2 — browse to `http://localhost:8765/mcp` and you'll see one
server card:

```
┌────────────────────────────────────────────────────────────┐
│  demo                  → programmer       [connected]   3 │
│  $ python3 infra/demo-mcp-server/server.py                 │
│  ▸ show 3 tools                                            │
└────────────────────────────────────────────────────────────┘
```

Expand the tools list and `demo_notes_append` shows up flagged
destructive (yellow border + ShieldAlert icon).

Step 3 — ask the programmer agent to use it. From the Chat tab:

```
"Append a note saying 'olympus demo working' via the demo server."
```

What happens behind the scenes:

1. The LLM router picks `programmer`.
2. The programmer's StructuralAgent sees `demo_notes_append` in its
   tool list (registered at startup, no LLM involvement).
3. The agent decides to call it.
4. `gate_tools` recognises `demo_notes_append` in `destructive_verbs`
   and routes through `QueueApprovalHook`.
5. An approval card surfaces in the right sidebar:
   `programmer → demo_notes_append`.
6. You click **Approve**. The agent's invoke now calls back into
   `MCPClient.call_tool("notes_append", {"text": ...})`.
7. The demo server appends the note and returns text.
8. The agent's audit log records both the approval and the tool
   result; the dashboard's telemetry footer ticks up.

Step 4 — verify with the read-only tool:

```
"List every note via the demo server."
```

No approval needed (read-only), the result appears inline.

## Programmatic surface

Everything you need is in `libs/agentlib/src/agentlib/mcp.py`. Import
from the top-level `agentlib` package:

```python
from agentlib import (
    MCPServerConfig,
    MCPClient,
    StdioTransport,
    MockTransport,
    register_mcp_tools,
    parse_command_string,
)
```

### `MCPServerConfig`

| Field         | Type            | What it's for                                                                |
|---------------|-----------------|------------------------------------------------------------------------------|
| `name`        | `str`           | Human label + tool-name prefix.                                              |
| `command`     | `str`           | Subprocess command (e.g. `"python3"`, `"npx"`).                              |
| `args`        | `list[str]`     | Subprocess args.                                                             |
| `env`         | `dict[str,str]` | Merged into subprocess env.                                                  |
| `cwd`         | `Optional[str]` | Subprocess working dir.                                                      |
| `destructive` | `set[str]`      | Tool names that must go through ApprovalHook. Match the *unprefixed* name.   |

`parse_command_string("npx -y @scope/server-fs /tmp")` returns
`("npx", ["-y", "@scope/server-fs", "/tmp"])` if you'd rather hand
a single CLI string.

### `register_mcp_tools(spec, config, client=None)`

Connect, list tools, wrap them as langchain `StructuredTool`s, append
to `spec.tools`. Destructive names from `config.destructive` are
lifted to `spec.destructive_verbs` with the prefix applied (so the
runtime sees the same name register_mcp_tools added). Pass `client=`
in tests to use `MockTransport`; in prod it builds a `StdioTransport`
itself.

### `MockTransport` (for tests)

```python
def handler(msg):
    method = msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "serverInfo": {"name": "fake", "version": "0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {
            "tools": [{"name": "ping", "description": "x", "inputSchema": {"type": "object"}}],
        }}
    # ... etc

client = MCPClient(MockTransport(handler))
client.initialize()
register_mcp_tools(spec, config, client=client)
```

That's the test path the agentlib + dashboard test suites use — no
subprocess required, fast, deterministic.

## Dashboard endpoints

| Endpoint                          | Body                       |
|-----------------------------------|----------------------------|
| `GET /mcp/servers`                | `{servers: [<summary>...]}`|
| `GET /mcp/servers/{name}/tools`   | `{name, tools: [<descriptor>...]}` |

Server summary fields: `name`, `target_agent`, `command` (summary
string), `tool_count`, `tools` (name list), `destructive` (sorted),
`status` (`connected` / `error`), `error` (when status is `error`).

Tool descriptors carry the full `description` + `inputSchema` so the
UI can render arg fields.

## Failure isolation

A misbehaving MCP server lands as `status="error"` in the registry
with the exception in `error`. Olympus does **not** crash if one
server is unreachable — other servers still register, native agents
still work. The MCP tab's error card surfaces the failure string so
you can see what went wrong without `kubectl logs`.

`StdioTransport` also drains the server's `stderr` to
`logger.warning` (not the dashboard log file) so a crashing third-
party server is debuggable without losing output.

## What's not here yet

- HTTP and SSE transports. Stdio + JSON-RPC is the protocol baseline;
  the other transports layer on top of the same JSON-RPC envelope.
- Resources (`resources/list`, `resources/read`). Tools are the
  immediate-value primitive; resources can come next.
- Runtime add/remove of servers via the dashboard. Servers are
  wired at startup for v1; the registry isn't mutable post-init.
- Server-initiated sampling / notifications beyond `initialized`.

## Tests

- `libs/agentlib/tests/test_mcp.py` — 25 tests (envelope shape,
  handshake, tools/list, tools/call, adapter, register, gate_tools
  round-trip, parse_command_string).
- `agents/dashboard/tests/test_dashboard_server.py` — 7 tests for
  the `/mcp/servers` endpoints and the `_wire_mcp_servers` helper.
- `agents/dashboard/frontend/src/pages/MCPPage.test.tsx` — 8 tests
  for the UI page.

All pass against `MockTransport`; the real `StdioTransport` is
exercised by running this guide's worked example against the demo
server.
