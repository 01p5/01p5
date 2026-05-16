# Olympus demo MCP server

Pure-Python, stdlib-only, single-file MCP server for the W9-10 demo.
Speaks JSON-RPC 2.0 over stdio per the
[Model Context Protocol spec](https://modelcontextprotocol.io/) (revision
`2024-11-05`).

## Tools

| Name                | Destructive? | What it does                                              |
|---------------------|--------------|-----------------------------------------------------------|
| `counter_increment` | no           | Increment a server-side counter, return its new value.    |
| `notes_append`      | **yes**      | Append a note to the server's in-memory notebook.         |
| `notes_list`        | no           | Return every note in insertion order.                     |

State is process-local — every `python3 server.py` invocation starts with
counter=0 and an empty notebook, so the demo is reproducible.

## Run standalone

```bash
python3 infra/demo-mcp-server/server.py
```

It'll sit on stdin waiting for JSON-RPC requests. Type:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
```

…and it responds with its capability block. Use Ctrl+D to close.

## Plug it into Olympus

```python
from agentlib import MCPServerConfig
from dashboard.server import build_default_server

cfg = MCPServerConfig(
    name="demo",
    command="python3",
    args=["infra/demo-mcp-server/server.py"],
    destructive={"notes_append"},  # tool name without the prefix
)

srv = build_default_server(mcp_servers=[
    {"name": "demo", "target_agent": "programmer", "config": cfg},
])
srv.serve()
```

What you'll see after registration:

- The Programmer agent has three new tools: `demo_counter_increment`,
  `demo_notes_append`, `demo_notes_list`.
- The dashboard's **MCP** tab shows the server card with the three
  tools listed. `demo_notes_append` is tagged destructive in yellow.
- Asking the programmer to "append a note saying hello" goes through
  the approval queue before the demo server sees the call.

## Smoke-test end-to-end without the dashboard

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, 'libs/agentlib/src')
from agentlib import MCPClient, StdioTransport, MCPServerConfig

cfg = MCPServerConfig(
    name="demo",
    command="python3",
    args=["infra/demo-mcp-server/server.py"],
)
client = MCPClient(StdioTransport(cfg))
client.initialize()
print("tools:", [t["name"] for t in client.list_tools()])
print(client.call_tool("counter_increment", {"by": 5}).text)
print(client.call_tool("notes_append", {"text": "hello"}).text)
print(client.call_tool("notes_list", {}).text)
client.close()
PY
```

## Limitations

- Single-threaded (one request at a time). Olympus's `MCPClient`
  matches that model, so it's fine for the demo.
- No resources / prompts / sampling — tools only.
- State doesn't survive a process restart. That's a feature for a
  demo, not for production.
