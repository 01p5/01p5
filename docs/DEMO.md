# Olympus — class presentation demo script

A chronological walkthrough you can present from. Each section
exercises one shipped feature in order, takes a couple of minutes,
and stacks on the prior section's state so the demo builds a story
rather than jumping around.

Designed to run end-to-end in **~15 minutes** against a fresh local
clone — no live cluster required (the live deploy at
`http://10.0.10.30/` predates W7-8 and isn't re-rolled).

## Setup (1 min)

Open three terminals. Term 1 is for the dashboard backend, term 2
for the frontend dev server, term 3 for the demo MCP server (we'll
launch it later).

```bash
# Term 1 — clone + install (~30s)
git clone git@github.com:01p5/01p5.git && cd 01p5
pip install -e libs/agentlib -e agents/olympus_cli \
            -e agents/sysadmin -e agents/programmer \
            -e agents/terraform -e agents/ansible \
            -e agents/dashboard
export OPENAI_API_KEY=sk-...
```

```bash
# Term 1 — start the dashboard backend
python -m dashboard.server                # listens on :8765
```

```bash
# Term 2 — start the frontend
cd agents/dashboard/frontend && npm install && npm run dev
```

Open <http://localhost:5173/>. You should see the Olympus splash and
the Chat tab focused.

**Talking points:**
- "This is one binary. The same orchestrator the dashboard serves,
  the CLI talks to over the same in-memory bus."
- "No external services for the demo — no Redis, no vector DB.
  Everything works against the local stdlib."

## Part 1 — A round-trip task (2 min)

In the Chat tab, type:

> *"List the pods in the default namespace and tell me if any are not running."*

What you'll narrate as it runs:

1. The Bus sidebar on the left fires: `task` → `orchestrator`.
2. The center bubble flips from "picking the right agent…" to
   "running on sysadmin agent…" — that's the LLMRouter deciding.
3. Sysadmin's `get_pods` tool runs, audit log on the right ticks up.
4. The summary lands in the chat. The bubble shows the timestamp,
   task-id chip, agent, and the cost chip (e.g. `$0.00043 · 1.2s`).
5. The telemetry footer at the bottom of the layout ticks to
   `1/1 tasks · $0.00043 spent`.

**Talking points:**
- "Every tool call hit the audit log — pre-execution + post — so
  there's no `kubectl` we ran that isn't recorded."
- "The cost chip + telemetry footer are live — every settled task
  contributes."

## Part 2 — Destructive flow + approval queue (2 min)

Now ask for something destructive:

> *"Spawn a throwaway nginx pod called demo-pod and then delete it."*

What to point at:

1. Sysadmin agent investigates first (multiple `get_pods` calls in
   the audit log).
2. The Approval Queue panel in the right sidebar fires an
   `sysadmin → delete_pod` card.
3. **Don't click anything yet.** The center bubble shows
   "awaiting your approval — see the right sidebar". The bus
   sidebar shows the approval_request event.
4. Click **Approve**. The agent's invocation resumes.
5. Audit log gets a second `delete_pod` row, this time with
   `approved=true` and the result.

**Talking points:**
- "The agent literally cannot run `delete_pod` without a human
  signing off. That's `gate_tools` — the runtime wraps every tool
  whose name is in `destructive_verbs`."
- "There's no way the LLM can talk its way around this. The schema
  is enforced in Python, not in prompts."

## Part 3 — Memory + feedback loop (3 min)

The previous turn just landed in memory. Send a second, *similar*
task:

> *"List pods in the kube-system namespace."*

Watch the assistant bubble. Underneath the answer you should see a
small chip cluster labelled **seen before** containing a pill that
shows the truncated text of the first task. That's `MemoryChips`
pulling the top-K similar prior runs.

Hover the chip — the title attribute shows the agent, the outcome,
and any correction.

Now demo the feedback loop:

1. Click the 👍 button under one of the assistant bubbles. The
   text "saved" flashes next to the buttons.
2. Click the ✎ correction button on a different turn. Type:
   > "use --namespace=default by default — kube-system was a
   > one-off."

   Click ✓. The form collapses and the correction is saved.
3. Run another similar task:
   > *"Show me the pods."*

   The chip below should now show the corrected turn highlighted
   green — that's the +0.15 score boost from `feedback="good"`
   kicking in.

**Talking points:**
- "Memory uses Jaccard over token sets — no vector DB, dep-free.
  Production deployments can swap in `EmbeddingMemoryStore` for
  cosine over OpenAI embeddings by setting `OLYMPUS_MEMORY=embeddings`."
- "Good entries get a small boost. Bad entries are filtered out
  entirely — they stay in the store for audit, just never resurface
  in prompts. That kills the bad-pattern reinforcement risk."
- "Corrections ride into the prompt block so an agent literally
  sees 'User correction: …' for similar future queries."

## Part 4 — Rollback execute (2 min)

Switch to the Programmer tab. Click the **Dockerfile** generator,
fill in a service name + image + port, and hit **save to file**.
The approval queue fires for `write_file`; approve it. The Programmer
writes the Dockerfile to disk.

Back to the right sidebar — the **Rollback queue** panel now shows
one card:

```
┌─────────────────────────────────────────┐
│  programmer → delete_file               │
│  delete /tmp/Dockerfile (did not exist) │
│  from task ...                          │
│  [ Undo ]                               │
└─────────────────────────────────────────┘
```

(Or `write_file` with prior bytes if the file already existed.)

Click **Undo**. The approval queue fires again — this time for the
inverse op. Approve it. The Dockerfile is gone from disk.

**Talking points:**
- "Every successful destructive call captures its own inverse before
  it fires. The runtime calls the agent's `rollback_snapshots[tool_name]`
  callable, which returns a `RollbackPlan` describing the undo."
- "Executing a rollback is itself destructive — it routes through
  the same gate_tools machinery. You re-approve the undo. Olympus
  never assumes you want it, only that the option is available."

## Part 5 — MCP integration (3 min)

The dashboard supports third-party MCP servers. Switch to the **MCP**
tab — it'll be empty in a fresh run. Now wire the demo server.

```bash
# Term 1 — stop the existing dashboard (Ctrl+C), then:
python3 -c "
from agentlib import MCPServerConfig
from dashboard.server import build_default_server

cfg = MCPServerConfig(
    name='demo',
    command='python3',
    args=['infra/demo-mcp-server/server.py'],
    destructive={'notes_append'},
)
srv = build_default_server(mcp_servers=[
    {'name': 'demo', 'target_agent': 'programmer', 'config': cfg},
])
srv.serve()
import time
while True: time.sleep(60)
"
```

Refresh the dashboard. The MCP tab now shows:

```
┌──────────────────────────────────────────────────────┐
│  demo            → programmer    [connected]      3  │
│  $ python3 infra/demo-mcp-server/server.py           │
│  ▸ show 3 tools                                      │
└──────────────────────────────────────────────────────┘
```

Click "show 3 tools" — each tool renders with its description.
`demo_notes_append` shows a yellow destructive flag.

Now go back to Chat and ask:

> *"Append a note saying 'olympus demo working' via the demo server,
> then list every note."*

Watch the chain:

1. The Programmer agent picks up the request.
2. It sees `demo_notes_append` and `demo_notes_list` in its tool
   list — added at startup by `register_mcp_tools`.
3. `demo_notes_append` re-routes through the approval queue (you
   flagged it destructive in `MCPServerConfig.destructive`).
4. Approve it. The MCP server (a separate Python process, term 3
   conceptually) appends the note and returns text.
5. `demo_notes_list` runs without approval (read-only) and returns
   the note.

**Talking points:**
- "Olympus's safety guarantees apply to MCP tools the same way they
  apply to native ones. The integrator — not the server — flags
  what's destructive."
- "Two MCP servers can declare a tool called `read` without
  collision because every tool is prefixed by the server name."
- "If the MCP server crashes, Olympus doesn't — failures land as
  `status=error` on the MCP tab. Other servers keep working."

## Part 6 — Cost + audit closing argument (1 min)

Browse all four sidebar panels:

- **Approval queue:** empty (nothing pending; we approved everything).
- **Rollback queue:** the Dockerfile entry shows as **executed** (greyed).
- **Audit log:** every tool call, with approval decision and result
  truncated. This is the source of truth.
- **Telemetry footer:** `N tasks · $X spent · avg $Y/task · K tokens · Ws wall · sysadmin: 3× · programmer: 2×`

**Closing talking points:**
- "Everything an agent did is here. Approve, reject, rollback —
  the human is always the final say on anything that mutates state."
- "Total cost for this whole demo: under a cent. That's the
  one-person-DevOps thesis — a tool that pays for itself."

## Total demo time: ~14 minutes

Add a 1-minute "What's next" wrap (alpha-tester invites, the W7-8
intelligence layer's open paths — better rollback for Terraform /
Ansible verbs, MCP HTTP transport, multi-user RBAC) and you're in
the 15-minute slot.

## Recovery cheat sheet (in case something flakes mid-demo)

| Symptom                                  | Fix                                                                  |
|------------------------------------------|----------------------------------------------------------------------|
| Frontend won't load                      | `cd agents/dashboard/frontend && npm install && npm run dev` again.   |
| MCP demo server unreachable              | `ps -fA \| grep demo-mcp` — kill stale; the dashboard auto-reconnects on restart with `status=error → connected`. |
| Approval queue empty but card expected   | The agent is still in the investigation phase — wait ~30s. Worst case, the `result_timeout` ticks and the task fails cleanly. |
| Memory chips don't show up               | First similar task — there's no prior entry yet. Run the task once, then run a similar one. |
| Rollback panel empty                     | Only successful destructive calls capture a rollback. A rejected call doesn't (correctly). |

## What's NOT in the demo

- Live cluster (10.0.10.30) — the deploy predates W7-8.
- Terraform / Ansible live verb (no rollback snapshots declared yet
  on those agents).
- HTTP-transport MCP server.

These are all in the W9-10 follow-up list, not the shipped feature
set.
