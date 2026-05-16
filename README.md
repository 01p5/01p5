# Olympus

Multi-agent DevOps system: one human, five LLM agents, real infrastructure.

Built for [CS 153 – Frontier Systems](https://www.classes.cs.chicago.edu/) as a one-person frontier lab. Live at <https://0lympu5.com> (intranet endpoint: <http://10.0.10.30/>).

> The vibe-coding tools enable anyone to build anything they can imagine, but keeping it running is still a DevOps problem. Olympus is the smallest viable answer: built by one person, used by one person, to operate infrastructure that used to take a whole DevOps team.

```
        ┌────────────────────────────────────────────────┐
        │   Human interfaces                             │
        │   ─ Web dashboard (React)  ─ CLI (olympus)     │
        └────────────────────┬───────────────────────────┘
                             │
                  ┌──────────▼──────────┐
                  │     Orchestrator    │   ← LLM router
                  │   (libs/agentlib)   │     + approval queue
                  └──────────┬──────────┘
                             │
            ┌────────┬───────┼────────┬────────┐
            │        │       │        │        │
            ▼        ▼       ▼        ▼        ▼
        Sysadmin  Programmer Terraform Ansible (Networking — W8)
         kubectl   files+    plan/      playbooks
          + logs   helm      apply      + modules
```

## Status

| Layer | State |
|-------|-------|
| `libs/agentlib` (SDK) | stable; 17 unit tests |
| 4 agents (sysadmin / programmer / terraform / ansible) | each gated, audited, smoke-tested |
| Dashboard (HTTP API + React SPA) | running on a 4-node k8s cluster |
| CLI | `olympus "..."` dispatches via the same orchestrator |
| Tests | **289 total** — 146 frontend (vitest) + 120 backend (pytest) + 23 E2E (Playwright). CI green. |
| Live deploy | Proxmox → 4× Ubuntu VMs → kubeadm + Calico → Helm chart |

## Quick start

### 1. Run the agent CLI locally (no cluster required)

```bash
# clone + install the SDK + CLI in editable mode
git clone git@github.com:01p5/01p5.git && cd 01p5
pip install -e libs/agentlib -e agents/olympus_cli \
            -e agents/sysadmin -e agents/programmer \
            -e agents/terraform -e agents/ansible

# point at an LLM provider
export OPENAI_API_KEY=sk-...        # or ANTHROPIC_API_KEY=...

# dispatch a task — the LLM router picks the agent
olympus "list pods in the default namespace"
olympus "write me a Dockerfile for a python flask app on port 8080"
olympus --router=manual "run terraform plan in infra/terraform/pve"
```

Output is the agent's structured `AgentResult` as JSON. Destructive verbs (`delete_pod`, `tf_apply`, `run_playbook`, `write_file`, `edit_file`) prompt on stdin for approval before they fire.

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

Open <http://localhost:5173/>.

### 3. Drive the live cluster

The live system is on the dev-VM intranet at `http://10.0.10.30/`. See [`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md) for the full runbook (every endpoint, every probe that's been exercised, known issues, tear-down).

### 4. Run the test suite

```bash
# Backend — 120 tests, ~20s
pytest libs/agentlib agents/dashboard/tests/test_dashboard_server.py \
       agents/programmer/tests agents/sysadmin/tests \
       agents/terraform/tests  agents/ansible/tests

# Frontend — 146 tests, ~2.5s
cd agents/dashboard/frontend && npm run test:run

# E2E (live cluster required) — 23 tests, ~3min
OLYMPUS_LIVE_E2E=1 KUBECONFIG=$HOME/.kube/config \
    pytest agents/dashboard/tests/test_dashboard_e2e.py
```

CI runs the first two on every push (`.github/workflows/ci.yml`).

## Overview

Olympus is shaped around four invariants — they show up in the agent contract, the runtime, and the dashboard wire format:

1. **Tool-gated execution.** An agent declares a fixed set of `langchain` tools and a fixed set of `destructive_verbs`. The runtime wraps every tool with `gate_tools(...)` so the agent literally cannot call something outside its declaration, no matter what the LLM emits. The Sysadmin agent cannot run `terraform apply` even if asked to.
2. **Human-in-the-loop on destructive ops.** Any tool whose name is in `destructive_verbs` re-enters the runtime through an `ApprovalHook` before it shells out. The hook can be `ConsoleApprovalHook` (CLI prompt), `QueueApprovalHook` (dashboard's red sidebar card), or `WebhookApprovalHook` (Slack-style). Approval state is part of every audit record.
3. **Append-only audit.** Every tool call is logged twice — once pre-execution (with the approval decision) and once post-execution (with the result). `JsonlAuditLogger` writes to disk; `InMemoryAuditLogger` is for tests.
4. **Bus-based observability.** The orchestrator publishes `task_started → agent_picked → tool_call → approval_request → tool_result → task_done` events to a `Bus` (in-memory default, Redis Streams optional). The dashboard's SSE endpoint just relays this bus to the browser; the CLI ignores it.

These four invariants are the entire safety story. Every component below is a different way of arranging them.

## Components

### `libs/agentlib/` — the SDK

The pure-Python core. No FastAPI, no React, no kubectl. If you want to build a sixth agent, this is the only thing you import.

| Module | Responsibility |
|--------|----------------|
| `spec.py` | `AgentSpec`, `TaskMessage`, `AgentResult`, `AgentContext`, `ApprovalHook`, `AuditLogger`, `CostBreakdown` — the agent contract |
| `runtime.py` | `gate_tools`, `JsonlAuditLogger`, `ConsoleApprovalHook`, `_preview_diff` (the unified-diff renderer for write_file/edit_file approval cards) |
| `main.py` / `streaming.py` | `StructuralAgent` / `StreamingAgent` — LangGraph wrappers that bind a system prompt, a response model (pydantic), and a tool list into one `invoke()` call |
| `orchestrator.py` | `Orchestrator`, `Router`, `ManualRouter` (keyword routing — deterministic, offline-safe), `LLMRouter` (calls an LLM to pick the agent) |
| `plan.py` | `Plan`, `PlanStep`, `PlanResult` — for multi-step decomposition (W7+ work) |
| `models.py` | One-liners for every supported model (`gpt5_mini`, `claude45`, `ollama(...)`, `vllm_qwen3(...)`) |
| `bus.py` / `bus_redis.py` | `InMemoryBus` (default), `RedisStreamsBus` (multi-pod future) |
| `approval_queue.py` / `approval_webhook.py` | Two non-CLI `ApprovalHook` implementations |
| `budget.py` | `BudgetGuard` — token/dollar ceiling enforced per task |

See [`docs/AGENT_SPEC.md`](docs/AGENT_SPEC.md) for the contract every agent implements and [`docs/BUS_DECISION.md`](docs/BUS_DECISION.md) for the bus design rationale.

### Agents (`agents/*/`)

Each agent is a tiny package: a `tools.py` of `@tool`-decorated functions and an `agent.py` declaring an `AgentSpec` subclass. They're all parallel — once you've read one, the rest are a 30-second skim.

| Agent | Read-only tools | Destructive verbs | Notes |
|-------|----------------|-------------------|-------|
| **Sysadmin** (`agents/sysadmin/`) | `get_pods`, `get_nodes`, `describe_pod`, `get_logs`, `get_events` | `delete_pod` | The reference implementation. The other three agents copy this shape. |
| **Programmer** (`agents/programmer/`) | `read_file`, `generate_dockerfile`, `generate_docker_compose`, `generate_helm_values`, list helpers | `write_file`, `edit_file` | `edit_file` uses Claude Code's `old_string`/`new_string` exact-match shape and surfaces a unified diff to the approval card. |
| **Terraform** (`agents/terraform/`) | `tf_init`, `tf_plan`, `tf_validate`, `tf_show` | `tf_apply`, `tf_destroy` | Defends `working_dir` before shelling out. |
| **Ansible** (`agents/ansible/`) | `list_inventory`, `check_playbook` (`--check` mode) | `run_playbook`, `run_module` | `module_args` not `args` — `args` collides with pydantic positionals. |

Each lives in its own `pyproject.toml` so you can `pip install -e` just the one you care about.

### `agents/dashboard/` — the web frontend

Two halves:

#### Backend (`src/dashboard/`)

A standard-library HTTP server (`server.py`) that wraps the orchestrator with a JSON API:

| Endpoint | Use |
|----------|-----|
| `POST /tasks` | Submit a natural-language task → `{task_id}` |
| `GET  /tasks/{id}` | Poll for the structured `AgentResult` |
| `GET  /events` (SSE) | Live bus stream — `task_started`, `tool_call`, etc. |
| `GET  /approvals` | Pending approval cards |
| `POST /approvals/{id}` | Approve / reject |
| `GET  /audit` | Append-only audit log (JSONL) |
| `GET  /tools` | Catalog of every tool every agent exposes (args schema, destructive flag) |
| `POST /tools/{agent}/{tool}` | Invoke a tool directly — no LLM in the loop, still gated |
| `GET  /stacks/terraform`, `/stacks/ansible` | Detected stacks/playbooks for the UI's dropdowns |

#### Frontend (`frontend/`)

React 18 + TypeScript + Vite + Tailwind. Five pages:

| Page | What it does |
|------|--------------|
| **Chat** | Three-column chat UI. Left: live bus. Center: streaming conversation, one bubble pair per task. Right: approval queue + audit. Threads prior turns as context for pronoun resolution. |
| **Kubernetes** | Pod/node/event tables with inline `logs` / `describe` / `delete` buttons. Goes through `POST /tools/sysadmin/...` so destructive tools still surface as approval cards. |
| **Terraform** | Stack cards with `init` / `validate` / `plan` / `apply`. Plan modal includes "Apply this plan" header action. |
| **Ansible** | Playbook cards with `check` / `run`. Inventory prefilled from `/stacks/ansible`. |
| **Programmer** | Three generators (Dockerfile / docker-compose / Helm values) with previews + save-to-file via gated `write_file`. |

The dark "security console" palette + Outfit/JetBrains Mono pair lives in `frontend/src/styles/`.

The multi-stage `Dockerfile` builds the SPA with Node 20 and bakes it into the Python image at `static/dist/`.

### `agents/olympus_cli/` — the terminal entry point

`olympus "..."` → loads all four agents → builds an orchestrator with `ConsoleApprovalHook` + `JsonlAuditLogger(~/.olympus/audit.jsonl)` → dispatches → prints the result as JSON. `--router=manual` swaps the LLM router for deterministic keyword routing (useful offline or in CI).

### `infra/` — the live deploy

| Path | Purpose |
|------|---------|
| `infra/terraform/aws/` | (W3–4) minimal AWS path — single EC2, K3s, deploy script. Currently mothballed in favour of the PVE path. |
| `infra/terraform/pve/` | The path that's actually deployed. 4 VMs on a Proxmox host (`10.0.10.20-23`), provisioned via Terraform's `bpg/proxmox` provider. |
| `infra/terraform/deployment/` | Inventory + SSH key emission. Output of `pve/` feeds straight into `infra/ansible/`. |
| `infra/ansible/master.yml`, `workers.yml`, `docker.yml`, `wg.yml` | The kubeadm bootstrap. End-to-end: empty Ubuntu VMs → working cluster with Calico + a private registry → ~12 minutes. |
| `infra/k8s/charts/olympus/` | Helm chart for the dashboard. Single Deployment (dashboard + orchestrator + bus + all four agent runtimes), Service + NodePort, `ClusterRole` for cluster-scoped reads. |

End state on the live system: Caddy on `10.0.10.30` proxies plain HTTP → cluster NodePort `30093`. See [`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md).

## Testing

Three layers, all run in CI except the live-cluster E2E (opt-in via `OLYMPUS_LIVE_E2E=1`):

```
libs/agentlib                 17 unit tests   — SDK core
agents/{4 agents}/tests       55 smoke tests  — gating, audit, approval semantics (LLM mocked)
agents/dashboard/tests/server 22 unit tests   — HTTP routing, path-traversal defense, SPA fallback
agents/dashboard/frontend     146 vitest      — every component, hook, page
agents/dashboard/tests/e2e    23 Playwright   — real browser → real cluster
```

The E2E suite spawns short-lived `e2e-target-<rand>` pods labelled `e2e-target=true` for the destructive flows; sweep any leaks with:

```bash
kubectl delete pod -l e2e-target=true --grace-period=0 --force
```

## Further reading

- [`PROJECT_PLAN.md`](PROJECT_PLAN.md) — the 10-week course plan with weekly deliverables and threat model.
- [`docs/AGENT_SPEC.md`](docs/AGENT_SPEC.md) — the `AgentSpec` contract every agent implements.
- [`docs/BUS_DECISION.md`](docs/BUS_DECISION.md) — why the bus looks the way it does (in-memory default, Redis Streams optional).
- [`docs/LIVE_DEMO.md`](docs/LIVE_DEMO.md) — runbook for the live deployment, every endpoint, what's been exercised, known issues.
- [`infra/k8s/README.md`](infra/k8s/README.md) — the Helm chart's deploy story.

## License

Academic / personal — built for CS 153 at the University of Chicago.
