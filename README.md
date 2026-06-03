# Olympus

Multi-agent DevOps system: one human, a coordinator, and a team of LLM specialists driving real infrastructure.

Built for [CS 153: Frontier Systems](https://cs153.stanford.edu/) at Stanford. Project domain: <https://0lympu5.com> (live demo: <https://demo.0lympu5.com>).

> 📖 **Full documentation — including a guided quick-start and every configuration option — lives at [docs.01p5.com](https://docs.01p5.com).** Start there if you want to understand or reproduce the system.

> The vibe-coding tools enable anyone to build anything they can imagine, but keeping it running is still a DevOps problem. Olympus is the smallest viable answer: built by one person, used by one person, to operate infrastructure that used to take a whole DevOps team.

```
        ┌────────────────────────────────────────────────┐
        │   Human interfaces                               │
        │   ─ Web dashboard (React, group chat)            │
        │   ─ CLI (olympus "...")                          │
        └────────────────────┬─────────────────────────────┘
                             │
                  ┌──────────▼──────────┐
                  │     Orchestrator    │   ← LLM router (CLI) /
                  │   (libs/agentlib)   │     main coordinator (chat)
                  │  + approval queue   │     + context bus + ticket store
                  └──────────┬──────────┘
                             │  dispatch / ask_agent
            ┌────────┬───────┼────────┬────────┬────────┐
            ▼        ▼       ▼        ▼        ▼        ▼
        Sysadmin Programmer Terraform Ansible  HPC   (+ terminal
         kubectl  files+    plan/     playbooks Slurm   companion)
        +ssh+logs helm      apply     +ssh      +GPU
```

Two interaction models share one orchestrator:

- **CLI** — `olympus "..."` routes the task to *one* specialist via the `LLMRouter` and runs it to completion. Deterministic `--router=manual` keyword routing is available offline.
- **Dashboard chat** — a **group-chat ticket**: the human, a `main` coordinator agent, and any specialists it pulls in are participants in one thread. `main` does no domain work itself; it `dispatch`es subtasks to specialists and `ask_agent`s them direct questions, narrating its reasoning as a live, interleaved "thinking" trace.

## Status

| Layer | State |
|-------|-------|
| `libs/agentlib` (SDK) | stable; memory, feedback, rollback, plan, bus (in-mem + Redis Streams), runtime, MCP (stdio + HTTP), group-chat tickets, self-protection, budget guard, streaming — all unit-tested |
| Specialist agents (sysadmin / programmer / terraform / ansible / hpc) | each tool-gated, audited, cost-tracked. 3 declare rollback snapshots (programmer: write/edit/delete_file; sysadmin: delete_pod ↔ apply_manifest; terraform: tf_apply ↔ tf_restore_state). |
| `main` coordinator + `terminal_companion` | `main` runs the group chat (dispatch + ask_agent + streamed thinking trace); `terminal_companion` is a pull-based observer for the in-browser SSH terminal. Both are non-routable. |
| Dashboard (HTTP API + React SPA) | group-chat UI + auth (Google OAuth / email OTP) + per-user accounting; pages for Chat, Sessions, Auditing, Capabilities (K8s/Terraform/Ansible/Programmer/HPC), Hosts/inventory, MCP, Terminal, Admin. |
| CLI | `olympus "..."` dispatches one specialist via the same orchestrator. |
| Tests | **~1,200 total** — 393 frontend (vitest) + 800 backend (pytest) + 23 E2E (Playwright, opt-in). CI green on every push. |
| Live deploy | **AWS**: kubeadm on EC2, Helm chart, fronted by TLS at <https://demo.0lympu5.com>. Provisioned/operated from a separate deployment repo; the in-repo `infra/` is the reference self-host path. |
| Self-protection | Olympus cannot manage the Kubernetes namespace or the VM hosts it runs on — a config-driven policy hard-denies those calls before they reach the approval queue, so a user can't escalate by editing the system that gates them. |

## Quick start

### 1. Run the agent CLI locally (no cluster required)

```bash
# clone + install the SDK + CLI + specialists in editable mode
git clone git@github.com:01p5/01p5.git && cd 01p5
pip install -e libs/agentlib -e agents/olympus_cli \
            -e agents/sysadmin -e agents/programmer \
            -e agents/terraform -e agents/ansible -e agents/hpc

# point at an LLM provider
export OPENAI_API_KEY=sk-...        # or ANTHROPIC_API_KEY=...

# dispatch a task — the LLM router picks the specialist
olympus "list pods in the default namespace"
olympus "write me a Dockerfile for a python flask app on port 8080"
olympus --router=manual "run terraform plan in infra/terraform/pve"
```

Output is the agent's structured `AgentResult` as JSON. Destructive verbs (`delete_pod`, `tf_apply`, `run_playbook`, `write_file`, `edit_file`, …) prompt on stdin for approval before they fire. The CLI loads the five routable specialists; the `main` coordinator + group chat are dashboard-only.

### 2. Run the dashboard locally

```bash
# backend (HTTP API on :8765)
pip install -e agents/dashboard
python -m dashboard.server                # → http://localhost:8765/healthz

# frontend (Vite dev server on :5173, proxies to :8765)
cd agents/dashboard/frontend
npm install
npm run dev
```

Open <http://localhost:5173/>. With no auth configured the backend runs in a dev bypass (no login); the live deploy enforces Google OAuth / email OTP.

### 3. Drive the live cluster

The live system runs on AWS at <https://demo.0lympu5.com> (kubeadm on EC2, behind a TLS reverse proxy). See [`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md) for the runbook — endpoints, what's been exercised, known issues. For a turnkey deploy you can reproduce yourself, see the sandbox deployment repo.

### 4. Run the test suite

```bash
# Backend — ~800 tests
pytest libs/agentlib agents/sysadmin agents/programmer agents/terraform \
       agents/ansible agents/hpc agents/main agents/dashboard \
       agents/olympus_cli agents/terminal_companion

# Frontend — 393 tests
cd agents/dashboard/frontend && npm run test:run

# E2E (live cluster required) — 23 tests, opt-in
OLYMPUS_LIVE_E2E=1 KUBECONFIG=$HOME/.kube/config \
    pytest agents/dashboard/tests/test_dashboard_e2e.py
```

CI runs the backend + frontend suites (each behind an 80% coverage gate) on every push (`.github/workflows/ci.yml`).

## Overview

Olympus is shaped around four invariants — they show up in the agent contract, the runtime, and the dashboard wire format:

1. **Tool-gated execution.** An agent declares a fixed set of `langchain` tools and a fixed set of `destructive_verbs`. The runtime wraps every tool with `gate_tools(...)` so the agent literally cannot call something outside its declaration, no matter what the LLM emits. The Sysadmin agent cannot run `terraform apply` even if asked to.
2. **Human-in-the-loop on destructive ops.** Any tool whose name is in `destructive_verbs` re-enters the runtime through an `ApprovalHook` before it shells out. The hook can be `ConsoleApprovalHook` (CLI prompt), `QueueApprovalHook` (dashboard's inline approval card), or `WebhookApprovalHook` (Slack-style). Approval state is part of every audit record. A `SelfProtectionPolicy` sits *in front of* approval: calls that target Olympus's own cluster/hosts are hard-denied, not queued.
3. **Append-only audit.** Every tool call is logged twice — once pre-execution (with the approval decision) and once post-execution (with the result). `JsonlAuditLogger` writes to disk; `InMemoryAuditLogger` is for tests.
4. **Bus-based observability.** The orchestrator publishes task/agent/tool/approval/result events to a `Bus` (in-memory default, Redis Streams optional). The dashboard projects this bus into a per-ticket transcript (`TicketStore`) and streams it to the browser over SSE.

These four invariants are the entire safety story. Every component below is a different way of arranging them.

## Components

### `libs/agentlib/` — the SDK

The pure-Python core. No web framework, no React, no kubectl. If you want to build another agent, this is the only thing you import.

| Module | Responsibility |
|--------|----------------|
| `spec.py` | `AgentSpec`, `TaskMessage`, `AgentResult`, `AgentContext`, `ApprovalHook`, `AuditLogger`, `CostBreakdown`, `cost_from_agent` — the agent contract |
| `runtime.py` | `gate_tools`, `JsonlAuditLogger`, `ConsoleApprovalHook`, the unified-diff renderer for write/edit approval cards |
| `main.py` / `streaming.py` | `StructuralAgent` (structured output) / `StreamingAgent` (raw-token streaming) — LangGraph wrappers that bind a system prompt, a tool list, and (for structural) a pydantic response model into one call. Both track per-instance cost. |
| `orchestrator.py` | `Orchestrator`, `Router`, `ManualRouter` (keyword routing), `LLMRouter` (LLM picks the agent), `dispatch_to` + per-ticket dispatcher/resolver closures, ticket lifecycle (`close_ticket`/`discard_ticket`), per-turn cost aggregation |
| `ticket.py` | `TicketStore`, `TicketEvent`, `TicketKind` — the group-chat transcript; `ask_agent` tool builder |
| `memory.py` | `MemoryStore` protocol + `Null`/`InMemory`/`Jsonl`/`Embedding` backends, feedback annotation |
| `rollback.py` | `RollbackPlan`, `RollbackStore` — per-verb inverse capture |
| `mcp.py` | MCP client: `MCPServerConfig`, `StdioTransport` + `HttpTransport`, `MCPClient`, `register_mcp_tools` |
| `self_protection.py` | `SelfProtectionPolicy` — hard-deny calls targeting Olympus's own namespace/nodes |
| `inventory.py` | `InventoryStore` — user-managed hosts + SSH keys feeding ansible/sysadmin `ssh_run` |
| `models.py` | one-liners for supported models (`gpt55`, `gpt5_mini`, `claude45`, `ollama(...)`, `vllm_qwen3(...)`) |
| `bus.py` / `bus_redis.py` | `InMemoryBus` (default), `RedisStreamsBus` (multi-process) |
| `approval_queue.py` / `approval_webhook.py` | non-CLI `ApprovalHook` implementations |
| `budget.py` | `BudgetGuard` — token/dollar ceiling enforced per task |

See [`docs/AGENT_SPEC.md`](docs/AGENT_SPEC.md) for the contract, [`docs/BUS_DECISION.md`](docs/BUS_DECISION.md) for the bus rationale, and [`docs/SUBAGENTS_PLAN.md`](docs/SUBAGENTS_PLAN.md) for the group-chat model.

### Agents (`agents/*/`)

Each agent is a tiny package: a `tools.py` of `@tool`-decorated functions and an `agent.py` declaring an `AgentSpec` subclass.

| Agent | Read-only tools | Destructive verbs | Notes |
|-------|----------------|-------------------|-------|
| **Sysadmin** (`agents/sysadmin/`) | `get_pods`, `get_nodes`, `describe_pod`, `get_logs`, `get_events` | `delete_pod`, `ssh_run` | The reference implementation. `ssh_run` is injected only when an `InventoryStore` is wired. |
| **Programmer** (`agents/programmer/`) | `read_file`, `generate_dockerfile`, `generate_docker_compose`, `generate_helm_values`, list helpers | `write_file`, `edit_file`, `delete_file` | `edit_file` uses Claude-Code-style `old_string`/`new_string` exact-match and surfaces a unified diff to the approval card. |
| **Terraform** (`agents/terraform/`) | `tf_init`, `tf_plan`, `tf_validate`, `tf_show` | `tf_apply`, `tf_destroy` | Defends `working_dir` before shelling out. |
| **Ansible** (`agents/ansible/`) | `list_inventory`, `check_playbook` (`--check`) | `run_playbook`, `run_module` | Host-level introspection via the wired SSH inventory. |
| **HPC** (`agents/hpc/`) | Slurm queue + GPU health (via MCP) | gated Slurm ops | Only available when its MCP servers (slurm-mcp / gpu-mcp) are connected; advertises `prerequisites`. |

Two non-routable agents complete the roster: **`main`** (`agents/main/`) — the group-chat coordinator (`dispatch` + `ask_agent` + streamed thinking trace, no native tools); and **`terminal_companion`** (`agents/terminal_companion/`) — a read-only observer that answers questions about an in-browser SSH session's scrollback.

Each agent lives in its own `pyproject.toml` so you can `pip install -e` just the one you care about.

### `agents/dashboard/` — the web frontend

#### Backend (`src/dashboard/`)

A standard-library HTTP server (`server.py`) wrapping the orchestrator with a JSON + SSE API. Selected endpoints:

| Endpoint | Use |
|----------|-----|
| `POST /tickets/{id}/messages` | Post a human message into a group-chat ticket → runs the `main` coordinator |
| `GET  /tickets/{id}/events` (SSE) | The ticket transcript: human/agent messages, dispatches, tool calls, thinking, approvals |
| `GET  /tickets/{id}/stream` (SSE) | Live reply tokens for a ticket (ephemeral) |
| `POST /tickets/{id}/close` | Summarize the ticket to memory + discard its checkpoints |
| `POST /tasks` · `GET /tasks/{id}` | One-shot task submit/poll (router path) |
| `GET  /tools` · `POST /tools/{agent}/{tool}` | Tool catalog + direct gated invocation |
| `GET  /approvals` · `POST /approvals/{id}` | Pending approval cards / resolve |
| `GET  /audit` · `POST /memory/{id}/feedback` | Audit log / 👍👎+correction on a run |
| `GET  /rollback` · `POST /rollback/{id}/execute` | Captured rollbacks / gated undo |
| `GET  /telemetry` | Cost rollups (totals, by-agent, by-status) |
| `GET/POST /mcp/servers` | Wired MCP servers + their tool catalogs |
| `GET/POST /inventory/hosts` · `/inventory/keys` | User-managed hosts + SSH keys |
| `GET  /terminal/sessions` · `/ws` | In-browser SSH terminal sessions |
| `GET  /gpu` · `/slurm` | HPC dashboards |
| `GET  /admin/accounting` · `/admin/activity` · `/admin/limits/...` | Per-user spend + caps (admin only) |
| `GET  /me` · `/auth/google/...` · `/auth/email/...` | Session + Google OAuth + email OTP |

#### Frontend (`frontend/`)

React 18 + TypeScript + Vite + Tailwind. Pages:

| Page | What it does |
|------|--------------|
| **Chat** | The group-chat ticket. The `main` coordinator's reply forms in-thread; dispatch chips (`main → sysadmin`), tool-call chips, an interleaved 💭 thinking trace, and inline approval cards render chronologically. Left rail = past sessions. |
| **Sessions** | Browse / resume past tickets. |
| **Auditing** | The append-only audit log, click-through to per-call detail. |
| **Capabilities** | Sub-tabs for **Kubernetes** (pod/node/event tables + logs/describe/delete), **Terraform** (stack cards: init/validate/plan/apply), **Ansible** (playbook check/run), **Programmer** (Dockerfile/compose/Helm generators), **HPC** (Slurm queue + GPU health). Destructive actions go through `POST /tools/...` so they still surface approval cards. |
| **Hosts** | Inventory: user-managed hosts + SSH keys (feeds ansible/sysadmin). |
| **MCP** | Every wired MCP server with status + lazy-loaded tool catalog (destructive flagged); includes the NetDB integration card. |
| **Terminal** | xterm.js SSH sessions in the browser; the terminal companion can answer questions about the scrollback. |
| **Admin** | Per-user daily cost caps + spending ledger (admin only). |

The dark "security console" palette lives in `frontend/src/styles/`. The multi-stage `Dockerfile` builds the SPA with Node and bakes it into the Python image at `static/dist/`.

### `agents/olympus_cli/` — the terminal entry point

`olympus "..."` → loads the five routable specialists → builds an orchestrator with `ConsoleApprovalHook` + `JsonlAuditLogger(~/.olympus/audit.jsonl)` → routes + dispatches → prints the result as JSON. `--router=manual` swaps the LLM router for deterministic keyword routing (offline / CI). A `textual` TUI lives in `tui.py`.

### `infra/` — reference self-host bring-up

The in-repo infrastructure path: a self-hosted kubeadm cluster + the Olympus Helm chart. **This is the reference/self-host path** — the live demo (`demo.0lympu5.com`) runs on AWS and is provisioned from a separate deployment repo.

| Path | Purpose |
|------|---------|
| `infra/terraform/aws/`, `infra/terraform/pve/` | cluster provisioning (AWS EC2 or Proxmox VMs) via Terraform |
| `infra/terraform/deployment/` | inventory + SSH key emission, fed into `infra/ansible/` |
| `infra/ansible/` | kubeadm bootstrap: empty Ubuntu → working cluster with Calico |
| `infra/k8s/charts/olympus/` | Helm chart: one Deployment (dashboard + orchestrator + bus + all agent runtimes incl. `main` + `hpc`), Service/NodePort, RBAC, optional sibling GPU/Slurm dashboards, self-protection `selfNodes` |

See [`infra/k8s/README.md`](infra/k8s/README.md) for the chart and [`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md) for the live AWS deployment.

## The intelligence layer

Four cooperating features that turn Olympus from "agents that run tools" into "a system that learns from prior runs and lets you undo what it did." All are off by default (`Null*` stores), so a deployment opts in by passing wired stores through `AgentContext`. Full depth doc: [`docs/INTELLIGENCE_LAYER.md`](docs/INTELLIGENCE_LAYER.md).

### Memory + retrieval (`libs/agentlib/memory.py`)

On every settled task the orchestrator writes a compact transcript to a `MemoryStore`; on the next task it retrieves the top-K most-similar prior runs scoped to the routed agent and prepends them as "treat as untrusted reference material." Backends: `JsonlMemoryStore` (lexical Jaccard, dep-free, default for tests/CI) and `EmbeddingMemoryStore` (OpenAI `text-embedding-3-small` + numpy cosine, default in prod). Env-driven: `OLYMPUS_MEMORY=disabled|embeddings|jsonl`, `OLYMPUS_MEMORY_PATH`.

### Feedback loop (`MemoryStore.annotate`)

👍 / 👎 / free-text correction on a past run. Retrieval drops "bad" entries from prompts entirely (kept for audit) and boosts "good" ones by +0.15; corrections ride into the retrieved prompt block. Endpoint `POST /memory/{task_id}/feedback`; UI under each settled chat turn.

### Per-verb rollback (`libs/agentlib/rollback.py`)

When a destructive tool succeeds, the runtime captures its inverse via the agent's `rollback_snapshots[tool](args)`. Three agents declare snapshots:

| Agent | Forward | Inverse |
|-------|---------|---------|
| Programmer | `write_file` (existing) / (new) / `edit_file` / `delete_file` | prior bytes / `delete_file` / pre-edit bytes / doomed bytes |
| Sysadmin | `delete_pod` | `apply_manifest` from scrubbed `kubectl get -o yaml` |
| Terraform | `tf_apply` | `tf_restore_state`: `state push` + `apply` |

Executing a rollback re-routes through `gate_tools`, so the undo re-prompts approval. `POST /rollback/{id}/execute`; UI `RollbackPanel`.

### Telemetry (`/telemetry` + cost aggregation)

`StructuralAgent` and `StreamingAgent` both accumulate per-invocation cost (USD + tokens) on the instance, so concurrent tasks don't race. In group chat, the coordinator's turn **aggregates the cost of every specialist it dispatched** (`aggregate_cost` + the orchestrator's per-ticket `_turn_costs` accumulator) so per-user accounting reflects the whole turn, not just `main`'s tokens — while a `cost_sink` feeds a per-agent breakdown for the dashboard's by-agent view. Surfaced via `GET /telemetry`, a per-turn cost chip, and a telemetry footer; rolled into per-user accounting on the Admin page.

## Model Context Protocol (MCP)

Third-party tools graft onto Olympus over MCP without touching core code. A server is wired with a `target_agent`, a transport (`StdioTransport` or `HttpTransport` for remote Streamable-HTTP servers), and an integrator-supplied destructive allowlist — its tools register *onto that specific agent* (prefixed) and flow through the same `gate_tools` + approval + self-protection machinery as native tools. Servers are declared at startup via the `OLYMPUS_MCP_SERVERS` env var or added at runtime via `POST /mcp/servers`. The production example is **NetDB** (IPAM/DNS/DHCP, ~32 tools over HTTP) grafted onto the sysadmin agent. Worked example + toy server: [`docs/MCP.md`](docs/MCP.md), [`infra/demo-mcp-server/`](infra/demo-mcp-server/).

## Testing

Three layers, all in CI except the opt-in live-cluster E2E:

```
libs/agentlib                343 unit tests  — SDK core (memory, rollback, plan, runtime, bus, MCP, tickets, self-protection)
agents/{5 specialists}/tests 111 smoke tests — gating, audit, approval, snapshot semantics
agents/main/tests             23 tests        — coordinator dispatch + streaming thinking parser
agents/dashboard/tests        251 unit tests  — HTTP routing + tickets + memory + rollback + telemetry + mcp + auth + admin
agents/{cli,terminal}/tests    72 tests        — CLI wiring + terminal companion
agents/dashboard/frontend     393 vitest      — every component, hook, page + UI integration
agents/dashboard/tests/e2e     23 Playwright  — real browser → real cluster (opt-in)
```

Backend total **~800 tests**, frontend **393**, each gated at 80% coverage on every push.

The E2E suite spawns short-lived `e2e-target-<rand>` pods for the destructive flows; sweep any leaks with:

```bash
kubectl delete pod -l e2e-target=true --grace-period=0 --force
```

## Further reading

- 📖 [**docs.01p5.com**](https://docs.01p5.com) — the hosted documentation site: project intro, guided quick-start, and every configuration option.
- [`PROJECT_PLAN.md`](PROJECT_PLAN.md) — the 10-week course plan, threat model, and what shipped.
- [`docs/AGENT_SPEC.md`](docs/AGENT_SPEC.md) — the `AgentSpec` contract every agent implements.
- [`docs/SUBAGENTS_PLAN.md`](docs/SUBAGENTS_PLAN.md) — the group-chat / coordinator model.
- [`docs/BUS_DECISION.md`](docs/BUS_DECISION.md) — why the bus looks the way it does.
- [`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md) — runbook for the live AWS deployment.
- [`docs/INTELLIGENCE_LAYER.md`](docs/INTELLIGENCE_LAYER.md) — memory + feedback + rollback + telemetry depth doc.
- [`docs/MCP.md`](docs/MCP.md) — MCP integration walkthrough + worked example.
- [`docs/DEMO.md`](docs/DEMO.md) — class-presentation script.

## AI usage & attribution

Per the CS 153 AI policy, this project was built with heavy use of AI tools, disclosed here.

- **[@clawdyyy](https://github.com/clawdyyy)** is my dedicated AI account. Both the development assistant (Claude, via Claude Code) and the automation/evaluation agent (openclaw) act through it — so commits, PRs, and automated runs attributed to `@clawdyyy` are AI-authored work I directed and reviewed. I made the design decisions, set the scope, and reviewed every change before it landed.
- AI was used across the project: writing and refactoring code, designing the agent/orchestrator architecture, authoring docs and tests, and driving the live deployment and end-to-end testing.

## Prior work & attribution

- **[Artemis](https://github.com/artemis-sysadmin/artemis)** — referenced for architectural patterns and prior art (see [`PROJECT_PLAN.md`](PROJECT_PLAN.md)). **Artemis is also my own project**, which I built independently; Olympus reuses ideas I developed there but is a separate, from-scratch implementation for this course. No third-party code was forked into this repository.

## License

Academic / personal — built for [CS 153: Frontier Systems](https://cs153.stanford.edu/) at Stanford University.
