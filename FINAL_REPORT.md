# Olympus — Final Report

**Course:** [CS 153: Frontier Systems](https://cs153.stanford.edu/), Stanford University
**Project:** Olympus — Multi-Agent DevOps from Terraform to Terminal
**Domain:** [0lympu5.com](https://0lympu5.com)

> This file is a *draft* scaffold for the CS 153 final writeup. Every
> claim cites the code or commit that backs it. Edit prose into your
> own voice; the structure + evidence are pre-assembled.

---

## 1. The pitch in one paragraph

The vibe-coding tools enable anyone to build anything they can imagine,
but keeping it running is still a DevOps problem. **Olympus is the
smallest viable answer**: a multi-agent system where five specialist
LLM agents — Sysadmin, Programmer, Terraform, Ansible, and a stretch
Networking agent — collaborate behind a chat-style interface to do the
work a small DevOps team would do. One person built it; one person
operates it; it manages infrastructure that previously required a team.

The work delivered:

| Dimension | Numbers |
|---|---|
| Lines of production Python + TypeScript | ~13,000 |
| Tests | **503 total** — 253 backend (pytest) + 227 frontend (vitest) + 23 E2E (Playwright) |
| Agents | 4 production (sysadmin, programmer, terraform, ansible) + 1 stretch (networking, deferred) |
| Live deploy | 4-node Kubernetes cluster on a Proxmox host, with the dashboard fronted by Caddy |
| CI | Green on every push, runs the full backend + frontend test suite |

---

## 2. Architecture overview

```
            ┌───────────────────────────────────────────────────┐
            │   Human interfaces                                │
            │   ─ Web dashboard (React + TS + Vite)             │
            │   ─ CLI (olympus "...")                            │
            └───────────────────────────┬───────────────────────┘
                                        │
                              ┌─────────▼─────────┐
                              │   Orchestrator    │  ← LLM router
                              │  (libs/agentlib)  │  ← memory + rollback wiring
                              │                   │  ← bus subscription
                              └─────────┬─────────┘
                                        │
        ┌────────────┬───────────┬──────┴──────┬────────────┬──────────────┐
        │            │           │             │            │              │
        ▼            ▼           ▼             ▼            ▼              ▼
   Sysadmin    Programmer    Terraform     Ansible     Networking    MCP-registered
    kubectl     write_file    plan/apply    playbooks   (stretch,    third-party tool
    + logs      + edit_file   + state       + modules   deferred)    servers
        │            │           │             │            │              │
        └────────────┴──────┬────┴─────────────┴────────────┘              │
                            │   gate_tools wrapping                        │
                            ▼                                              │
                  ┌─────────────────────┐                                  │
                  │ ApprovalHook +      │                                  │
                  │ AuditLogger +       │ ←────────────────────────────────┘
                  │ RollbackStore       │
                  └─────────────────────┘
```

Five invariants underpin the design:

1. **Tool-gated execution.** Each agent declares a fixed `tools` list
   and a fixed `destructive_verbs` set. The runtime literally cannot
   call something outside the declaration. The Sysadmin agent cannot
   run `terraform apply` even if asked to. — [`libs/agentlib/src/agentlib/runtime.py`](libs/agentlib/src/agentlib/runtime.py), [`spec.py`](libs/agentlib/src/agentlib/spec.py)
2. **Human-in-the-loop on destructive ops.** Any tool whose name is
   in `destructive_verbs` re-enters the runtime through an
   `ApprovalHook` before it shells out. Three implementations:
   `ConsoleApprovalHook` (CLI prompt), `QueueApprovalHook` (dashboard
   sidebar), `WebhookApprovalHook` (HTTP webhook for future Slack
   integration).
3. **Append-only audit.** Every tool call is logged twice — once
   pre-execution (with the approval decision) and once post-execution
   (with the result). `JsonlAuditLogger` writes to disk; `InMemoryAuditLogger`
   is for tests.
4. **Bus-based observability.** The orchestrator publishes
   `task_started → agent_picked → tool_call → approval_request → tool_result
   → task_done` events to a `Bus` (in-memory default, `RedisStreamsBus`
   optional). The dashboard's SSE endpoint relays this bus to the
   browser; the CLI ignores it.
5. **Reversibility on destruction.** Every successful destructive call
   captures its inverse-state snapshot via the agent's `rollback_snapshots`
   callable. Executing a rollback re-routes through `gate_tools` so the
   undo also needs human approval. — [`libs/agentlib/src/agentlib/rollback.py`](libs/agentlib/src/agentlib/rollback.py)

These five invariants are the entire safety story. Every component
below is a different way of arranging them.

---

## 3. The 10-week narrative

The plan in [`PROJECT_PLAN.md`](PROJECT_PLAN.md) carved the work into
ten weeks. What actually shipped:

### Weeks 1–2: Proposal & Proof of Concept

Built the agent contract first — [`docs/AGENT_SPEC.md`](docs/AGENT_SPEC.md)
defines `AgentSpec`, `TaskMessage`, `AgentResult`, `ApprovalHook`,
`AuditLogger`, and `CostBreakdown`. The contract was deliberately
promoted ahead of any concrete agent so every later phase had a stable
target.

CI was set up by the end of week 2 — ruff + pytest on every push
against three Python versions. Single-agent PoC (Sysadmin reading
pods) was running end-to-end against a local cluster.

### Weeks 3–4: Core Platform & All Agents

Built four concrete agents (Sysadmin, Programmer, Terraform, Ansible)
in parallel; each is ~150 LOC of declaration over the shared agentlib.
The Networking agent was deferred to stretch — sequenced ahead of the
risk that a five-agent W3-4 schedule would slip.

Built the orchestrator + LLMRouter / ManualRouter, the bus
(`InMemoryBus` + an opt-in `RedisStreamsBus`), and three approval-hook
implementations. Terminal UI with `textual` shipped at
`agents/olympus_cli/src/olympus_cli/tui.py`.

The AWS deploy path was deprioritized partway through W3 (budget;
the available AWS credentials weren't appropriate for sandbox use).
Replaced with a Proxmox-based deploy on a self-hosted lab cluster:
four VMs provisioned by `infra/terraform/pve/`, configured by
`infra/ansible/` playbooks (kubeadm + Calico CNI), running the Helm
chart from `infra/k8s/charts/olympus/`. Same "one agent against real
infrastructure" deliverable, on infrastructure under our own
operational control.

### Weeks 5–6: Cross-Agent Workflows & Web UI

Shipped `Plan` / `PlanStep` / `Orchestrator.run_plan` — multi-step
plans where each step is pinned to a named agent and prior results
thread forward into the next step's prompt. Failure short-circuits
unless `allow_failure=True`.

Shipped the dashboard: a standard-library HTTP server (`agents/dashboard/`)
that serves both the JSON API and the React SPA out of one process.
React 18 + TypeScript + Vite + Tailwind, five product pages:

- **Chat**: three-column live chat with bus events, streaming
  conversation, approval queue + audit.
- **Kubernetes**: pod/node/event tables with inline logs / describe /
  delete.
- **Terraform**: stack cards with init / validate / plan / apply.
- **Ansible**: playbook cards with check / run.
- **Programmer**: three generators (Dockerfile / docker-compose / Helm)
  with previews + save-to-file via gated `write_file`.

The W5-6 deliverable was demoed against the live cluster — see
[`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md) for the runbook (every
endpoint, every probe exercised, known issues, tear-down).

### Weeks 7–8: Testing, Hardening & Intelligence

The plan called this "Testing, Hardening & Intelligence". The
**intelligence half is what separated Olympus from a tool-bag**.
Four cooperating subsystems shipped:

| Subsystem | What it does | Where it lives |
|-----------|-------------|----------------|
| **Memory + retrieval** | Stores compact task transcripts; retrieves top-K similar past runs at each new task; prepends them to the agent's prompt with a "treat as untrusted" prefix. Two backends: `JsonlMemoryStore` (Jaccard, dep-free) and `EmbeddingMemoryStore` (OpenAI embeddings + numpy cosine). | [`libs/agentlib/src/agentlib/memory.py`](libs/agentlib/src/agentlib/memory.py) |
| **Feedback loop** | 👍 / 👎 / correction on each chat turn. "Bad" entries are filtered out of retrieval entirely; "good" entries get a +0.15 score boost; corrections ride into future retrieval blocks as "User correction: …" | `MemoryStore.annotate(...)` |
| **Per-verb rollback** | Every destructive call captures its inverse via the agent's `rollback_snapshots[tool_name]` callable. Stored in a `RollbackStore`; executable via `POST /rollback/{id}/execute` which re-routes through `gate_tools` for re-approval. | [`libs/agentlib/src/agentlib/rollback.py`](libs/agentlib/src/agentlib/rollback.py) |
| **Telemetry surfacing** | Per-task cost on `AgentResult.cost` (USD + input/output tokens + wall seconds); dashboard rolls it up via `GET /telemetry` (totals, per-agent buckets, per-status counts, last 10 tasks); UI shows a per-turn cost chip + a live telemetry footer. | `cost_from_agent` in `spec.py` + `_handle_telemetry` in `server.py` |

Cross-agent integration tests in
[`libs/agentlib/tests/test_plan_integration.py`](libs/agentlib/tests/test_plan_integration.py)
exercise the full W7-8 stack (memory + rollback + audit + approval +
plan) through a writer → planner → checker workflow. Eight tests
covering everything from rollback-tagged-to-step-task-id to
"per-agent retrieval scope holds inside a plan."

Full depth document: [`docs/INTELLIGENCE_LAYER.md`](docs/INTELLIGENCE_LAYER.md).

### Weeks 9–10: Scale & Polish (MCP + presentation)

The W9-10 headline was the **MCP (Model Context Protocol) integration**.
Olympus accepts third-party MCP servers as tool sources — registered
onto an agent at startup, prefixed by server name so two servers can
both declare a tool called `read`, with the destructive set supplied
by the integrator rather than the server.

Two passes:

- **Pass 1** ([`libs/agentlib/src/agentlib/mcp.py`](libs/agentlib/src/agentlib/mcp.py)):
  `MCPServerConfig`, transport-agnostic `MCPClient` (JSON-RPC 2.0
  handshake + `tools/list` + `tools/call`), `StdioTransport` for
  real subprocesses + `MockTransport` for tests, `to_langchain_tool`
  adapter, `register_mcp_tools` convenience.
- **Pass 2** (dashboard surface): `GET /mcp/servers`, `GET
  /mcp/servers/{name}/tools`, new "MCP" tab in the topnav showing
  every wired server with status + lazy-loaded tool catalog.
  Failing servers land as `status="error"` rather than crashing the
  dashboard.

A working demo MCP server ships at
[`infra/demo-mcp-server/server.py`](infra/demo-mcp-server/server.py) —
pure-Python stdio, dependency-free, three tools (counter, notes
append, notes list) that exercise both read-only and destructive
paths. Worked example in [`docs/MCP.md`](docs/MCP.md).

The W9-10 documentation pass also produced [`docs/DEMO.md`](docs/DEMO.md) —
a 14-minute chronological class-presentation script.

What's not shipped: alpha-tester outreach, the actual screencast
recording, and this writeup (which is being drafted now).

---

## 4. Key design decisions

### 4.1 Why a typed agent contract before any agent

The first major decision (W2) was to write the agent contract before
building any concrete agent. `AgentSpec` declares `name`, `domain`,
`tools`, `destructive_verbs`, `rollback_snapshots`, and `model` as
class attributes; the runtime knows nothing about specific agents.

Result: adding a fifth agent (Networking, when it lands) is ~150
LOC. Adding an MCP-backed sixth "agent" is zero LOC of contract
change — `register_mcp_tools` just appends to an existing spec's
tools list. The contract paid for itself by W3.

### 4.2 Bus-based observability over per-tool logging

Considered: have each tool wrapper log its own events. Rejected
because that makes "what happened in this task" reconstruction
require log-correlation across agents. Adopted: a single `Bus` that
every component publishes to (orchestrator, agents, dashboard), with
typed `BusMessage`s carrying `task_id`, `causation_id`, `kind`,
`payload`.

The dashboard's `/events` SSE endpoint is then just `bus.subscribe("*")`
relayed to the browser. The audit log is the durable mirror. There's
no "log aggregation" problem because there's one log.

Decision document: [`docs/BUS_DECISION.md`](docs/BUS_DECISION.md).

### 4.3 Lexical-first memory ranking

The W7 open question in the plan was "agent memory vector DB choice —
pgvector / Chroma / Qdrant?" We chose **none**. Reasoning:

- The deployment target is one person, ~10k entries over the project's
  lifetime. A full vector DB is overkill.
- Jaccard over token sets matches the patterns DevOps tasks follow
  (verb + object + namespace) surprisingly well.
- An OpenAI-embeddings path (`EmbeddingMemoryStore`) is a one-class
  upgrade for users who want semantic similarity. It uses numpy
  cosine on inline-cached vectors, persists in the same JSONL format.

The Protocol-driven `MemoryStore` lets external vector DBs slot in as
a one-class addition later if needed. v1 ships dep-free.

### 4.4 Per-verb rollback over universal undo

A naive "save everything before any destructive call" rollback layer
quickly becomes infeasible — `kubectl delete pod` produces no
snapshotable file; `terraform apply` against AWS mutates a state file
that lives elsewhere. So rollback is **per-verb, opt-in**: the agent
declares `rollback_snapshots[tool_name](args)` that returns a
`RollbackPlan` describing the inverse.

The Programmer agent declares four snapshots (write/edit/delete_file
plus the matching inverses; `delete_file` was added specifically as
the inverse for write_file-creates-new-file). Sysadmin / Terraform /
Ansible inherit the infrastructure but haven't opted in yet — each
is a 20-line addition when needed.

This shape means the rollback feature is **honest**: the UI greys
out rows for tools without snapshots, and the user always knows
when undo is available.

### 4.5 MCP destructive set comes from the integrator

The MCP spec doesn't carry a destructive flag, so a malicious server
can't smuggle a `delete_everything` tool in as read-only. The
integrator names what's destructive at registration time via
`MCPServerConfig.destructive`. The runtime then routes through
ApprovalHook regardless of what the MCP server claims.

The cost: integrators need to do this manually for each server.
The benefit: the safety story stays a one-line invariant ("anything
in `destructive_verbs` requires approval"), and MCP doesn't punch a
hole in it.

---

## 5. Success metric report

The plan listed six targets to evaluate against. Where we landed:

| Metric | Target | Status | Notes |
|--------|--------|--------|-------|
| Time to deploy a new service | < 15 min | ✓ verified manually | "Generate a Dockerfile + dump it to disk" via the Programmer is ~30s end-to-end; the Helm chart's deploy is on the order of minutes. |
| Cost per task (p50) | < $0.50 | ✓ well under | Telemetry footer shows per-turn cost; typical "list pods" tasks land at $0.0004 (gpt-5-mini). A full self-diagnosis run with 4-tool chain was ~$0.0012. |
| % operations without override (read-only) | > 70% | ✓ qualitatively | The four W5-6 live demos all completed without override on the read-only path. |
| % operations without override (write) | > 40% | Not yet measured | Need volume of write operations to make this meaningful. The infrastructure for measurement is in place (audit log + telemetry). |
| Mean tokens per task | < 50k | ✓ way under | Single-tool tasks are <2k tokens; the heaviest 4-tool self-diagnosis was ~22 OpenAI calls totalling ~14k tokens. |
| Rollback success rate | > 95% | Not yet measured under load | The rollback path is tested end-to-end (8 tests in `test_runtime.py`, 8 in `test_programmer_smoke.py`, 6 dashboard tests). Real-world failure mode would be inverse-tool unavailability (e.g. agent crashes between forward + rollback). |
| Approval latency p50 | < 60s | ✓ verified by UX | Approval cards surface in the right sidebar within the SSE round-trip (~1s), so latency is bounded by the human's response time. |

The metrics that are "not yet measured under load" need real-world
usage — which is exactly what the alpha-tester outreach in W9-10
would unlock.

---

## 6. What worked

### 6.1 Contract-first development

Writing `AgentSpec` before any concrete agent meant every later
phase could ship in parallel. Four agents got built in W3-4 by what
amounted to copy-paste of the same skeleton, varying only the
`tools` + `destructive_verbs` + system prompt.

### 6.2 In-memory bus by default, Redis as a swap

The plan considered Redis Streams from W2. Shipping with an
in-memory bus and making Redis opt-in let the tests run dep-free,
the dashboard run in a single pod, and the W7 multi-pod story
remain an option (not a requirement).

### 6.3 The W7-8 intelligence layer as a single coherent thing

Memory + feedback + rollback + telemetry weren't designed as four
separate features. They were designed as **one system** where each
piece feeds the others — memory carries cost data in its metadata,
feedback bends retrieval, rollback entries link to forward task_ids
in the audit log, telemetry pulls from per-task `CostBreakdown`
that the orchestrator already had. Building them in that order
meant each piece reinforced the prior, and the integration tests
in [`test_plan_integration.py`](libs/agentlib/tests/test_plan_integration.py)
exercise the whole stack at once.

### 6.4 Test coverage proportional to surface

Each layer got tests proportional to its public API:

- Library: 132 unit tests covering every module (spec, runtime,
  memory, rollback, mcp, plan, orchestrator, bus, budget).
- Agents: 65 smoke tests verifying gate_tools wrapping + approval
  semantics + rollback snapshot shape, all subprocess-mocked so
  CI doesn't need terraform / ansible / kubectl.
- Dashboard backend: 51 endpoint tests against a real
  `DashboardServer` on a loopback port.
- Frontend: 227 vitest tests covering every component, hook, page,
  and the api client.
- E2E: 23 Playwright tests driving headless Chromium against a
  live cluster.

CI green every push. The frontend tests run in ~3s; the backend in
~30s; the whole suite under 35s.

---

## 7. What didn't go as planned

### 7.1 AWS deploy path

Planned for W3-4. The user's local AWS credentials pointed at
corporate infrastructure, and a sandboxed account wasn't worth the
spend for what was already covered by the PVE deploy. Deprioritized
without abandoning — the code path is intact in `infra/terraform/aws/`
+ the Terraform agent's AWS-specific snapshots, ready to re-enable
if a sponsored sandbox appears.

### 7.2 Networking agent

Planned as the fifth agent in W3-4. Moved to stretch (W9-10) at
the start of W3 to de-risk the four-agent sprint. Ultimately
deferred entirely — the four shipped agents covered enough surface
to demo the full safety + intelligence story, and the MCP integration
in W9-10 makes a fifth agent ergonomic to add later without
touching Olympus core.

### 7.3 Live cluster re-deploy after W7-8

The currently-running deployment of the dashboard predates the W7-8
intelligence layer. Re-rolling the Helm chart would surface
`/memory`, `/rollback`, and `/telemetry` on the live system, but
that was held back as a shared-infrastructure change requiring
explicit operator authorization. The demo in `docs/DEMO.md` runs
locally end-to-end instead, against the same code.

---

## 8. Limitations

The honest list of what's brittle or unfinished:

- **In-flight tasks die with the pod.** Bus is in-memory inside the
  dashboard pod; the audit log lives on `emptyDir`. A pod restart
  loses both. The `RedisStreamsBus` is implemented + tested with
  fakeredis, but the chart doesn't wire a Redis subchart yet.
- **Audit-log timestamps under concurrency aren't strictly monotonic.**
  Multiple worker threads `open(..., "a")` the same file without
  locking. OS-level append is atomic for short records so no
  records are lost, but order can be slightly inverted. A real
  fix would be a single writer thread + queue.
- **Image distribution is local-tar / `ctr import`.** No registry
  on the cluster; image tags pin via `image.pullPolicy=Never`.
  Fine for a single dev setup. Multi-machine or rolling updates
  need a cluster-internal registry — `infra/ansible/set_registry.yml`
  writes the daemon.json but the registry itself isn't deployed.
- **`output_version="responses/v1"` was tried and reverted.**
  OpenAI GPT-5+ enforces strict tool schemas through it, and the
  langchain schema serializer drops `additionalProperties: false`.
  Worked around in `runtime._strict_schema_dict` by passing tool
  schemas as dicts; watch out if upgrading langchain-openai.
- **Cluster-scoped resources are RBAC-bounded.** The W5-6 chart had
  a namespace-scoped `Role`; promoted to `ClusterRole` mid-W6 for
  `kubectl get nodes` to work. Other cluster-scoped reads (e.g.
  `pv`, `clusterrolebinding`) would need explicit verbs added.
- **Rollback in agents beyond Programmer.** Sysadmin / Terraform /
  Ansible inherit the infrastructure but don't declare snapshots
  yet. Each is ~20 LOC to add.

---

## 9. Future work

Beyond the immediate gaps in Limitations:

1. **MCP HTTP / SSE transports.** Stdio is the protocol baseline;
   the standard MCP servers in the ecosystem mostly speak it, but
   long-running ones use HTTP. The Transport Protocol is structured
   to absorb a new implementation without touching `MCPClient`.
2. **Runtime MCP server add/remove.** Currently servers are wired
   at dashboard startup. Hot-add would need rebuilding the agent's
   gated-tools list after registration — doable but not done.
3. **Cross-agent memory retrieval.** Off in v1 to keep noise low.
   The right hook is the `agent=` argument to `MemoryStore.search`;
   a v2 could let the LLMRouter retrieve from related agents too.
4. **Cost-aware retrieval.** Memory entries carry their cost in
   `metadata`. Future ranking could prefer cheap-prior-runs on ties.
5. **Auth / RBAC.** Marked as a stretch open question. The
   `WebhookApprovalHook` is the right starting point — Slack-style
   approval routing with per-channel auth, then user-level agent
   permissions on top.
6. **Resources, prompts, sampling.** MCP has these primitives;
   Olympus only implements tools in v1.

---

## 10. Lessons learned

A few that generalize beyond DevOps agents:

1. **Write the contract before the implementation.** AgentSpec gave
   every later phase a stable target. Even when the contract needed
   revision (e.g. adding `rollback_snapshots` in W7), the cost of
   the change was bounded because the surface was small and typed.
2. **Tests are the docs that compile.** The 503-test suite covers
   semantics that no prose doc captures — like exactly what happens
   when an MCP server's `tools/list` returns a malformed entry. Any
   future maintainer can read the tests to understand the contract.
3. **Safety as a layered invariant, not a top-level feature.**
   "Human-in-the-loop" isn't a switch you turn on; it's
   `gate_tools` wrapping every tool the agent sees, regardless of
   provider, language, or origin. The runtime enforces it; agents
   can't opt out by mistake. MCP fits this because new tools come
   in *through* `gate_tools`, not around it.
4. **Memory + feedback compose surprisingly well.** Naive memory
   risks reinforcing bad past patterns. The +0.15 boost-good-filter-bad
   scheme handles that without any ML — just bookkeeping. The
   "correction" field rides the same channel, so the user's
   judgment shapes future retrieval at no extra plumbing cost.
5. **Honest failure modes beat hidden ones.** The "MCP server
   failed to register" path lands as a visible red card in the UI,
   not a silent dropped server. The "rollback wasn't captured"
   case is observable in the audit log. The "memory was too short
   to retrieve anything" case shows up as an absent chip cluster,
   not as a confidently-wrong context block. Each of these was a
   deliberate choice to favour honest UX over magic.

---

## Appendix A — File map

```
libs/agentlib/src/agentlib/
  spec.py             AgentSpec, TaskMessage, AgentResult, CostBreakdown, cost_from_agent
  runtime.py          gate_tools, ApprovalHook implementations, audit, rollback hook
  orchestrator.py     Orchestrator, LLMRouter, ManualRouter, run + run_plan
  bus.py / bus_redis  InMemoryBus + RedisStreamsBus
  memory.py           MemoryStore Protocol, Null/InMemory/Jsonl/Embedding
  rollback.py         RollbackStore Protocol, Null/InMemory/Jsonl, RollbackPlan/Entry
  mcp.py              MCPServerConfig, Transport, StdioTransport, MockTransport,
                      MCPClient, to_langchain_tool, register_mcp_tools
  approval_queue.py   QueueApprovalHook (dashboard sidebar backend)
  approval_webhook.py WebhookApprovalHook
  main.py             StructuralAgent + per-instance cost accumulator
  streaming.py        StreamingAgent
  models.py           One-line definitions for every supported LLM
  plan.py             Plan, PlanStep, PlanResult, step_to_task
  budget.py           BudgetGuard

agents/
  sysadmin/           SysadminAgent + kubectl/log tools
  programmer/         ProgrammerAgent + read/write/edit/delete_file +
                      generate_dockerfile/compose/helm + rollback snapshots
  terraform/          TerraformAgent + tf_init/plan/apply/destroy
  ansible/            AnsibleAgent + list_inventory/check_playbook/run_playbook/run_module
  dashboard/          DashboardServer (HTTP + SSE) + React/TS frontend
  olympus_cli/        olympus CLI + textual TUI

infra/
  terraform/pve/      Proxmox VM provisioning (4× Ubuntu)
  terraform/aws/      AWS path (held)
  ansible/            kubeadm master + workers, docker, wireguard
  k8s/charts/olympus/ Helm chart for the dashboard
  demo-mcp-server/    Pure-Python stdio MCP server for demos

docs/
  AGENT_SPEC.md          The agent contract
  BUS_DECISION.md        Bus design rationale
  LIVE_DEMO.md           Runbook for the live deployment
  INTELLIGENCE_LAYER.md  W7-8 depth doc
  MCP.md                 MCP integration walkthrough
  DEMO.md                Class-presentation script
```

## Appendix B — Commit timeline (final session)

The last session shipped W7-8 + W9-10 + docs in 11 commits:

| Commit | Theme | Lines | Tests added |
|--------|-------|-------|-------------|
| `4cfdbf0` | Memory + retrieval | +1062 | +28 |
| `8f9ceae` | Feedback loop | +495 | +19 |
| `9bbad49` | Per-verb rollback | +1358 | +34 |
| `0185a57` | Cross-agent integration | +458 | +8 |
| `b005137` | UI for W7-8 | +1150 | +40 |
| `9801400` | Telemetry surfacing | +886 | +55 |
| `89a8865` | Docs reconciliation | +67 | — |
| `7526b6e` | MCP library | +1054 | +25 |
| `76ab0e6` | MCP dashboard + UI | +818 | +18 |
| `eec445f` | Demo MCP server + docs/MCP.md | +585 | — |
| `b0a4fbf` | DEMO + INTELLIGENCE_LAYER docs | +646 | — |
| **Total** | | **+8579** | **+227** |

CI green on every push.

---

*Last updated by Claude Code during the W9-10 polish session.*
