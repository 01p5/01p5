# Olympus

Multi-agent DevOps system: one human, a coordinator, and a team of LLM specialists driving real infrastructure.

Built for [CS 153: Frontier Systems](https://cs153.stanford.edu/) at Stanford. Project domain: <https://01p5.com> (live demo: <https://demo.0lympu5.com>).

> **Full documentation — including a guided quick-start and every configuration option — lives at [docs.01p5.com](https://docs.01p5.com).** Start there if you want to understand or reproduce the system.

> The vibe-coding tools enable anyone to build anything they can imagine, but keeping it running is still a DevOps problem. Olympus is the smallest viable answer: built by one person, used by one person, to operate infrastructure that used to take a whole DevOps team.

```
        ┌──────────────────────────────────────────────────┐
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

- **Dashboard chat** — a **group-chat ticket**: the human, a `main` coordinator agent, and any specialists it pulls in are participants in one thread. `main` does no domain work itself; it `dispatch`es subtasks to specialists and `ask_agent`s them direct questions, narrating its reasoning as a live, interleaved "thinking" trace.
- **CLI** — `olympus "..."` routes the task to *one* specialist via the `LLMRouter` and runs it to completion. Deterministic `--router=manual` keyword routing is available offline.

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

## The four invariants

Olympus is shaped around four invariants — they show up in the agent contract, the runtime, and the dashboard wire format:

1. **Tool-gated execution.** An agent declares a fixed set of `langchain` tools and a fixed set of `destructive_verbs`. The runtime wraps every tool with `gate_tools(...)` so the agent literally cannot call something outside its declaration, no matter what the LLM emits. The Sysadmin agent cannot run `terraform apply` even if asked to.
2. **Human-in-the-loop on destructive ops.** Any tool whose name is in `destructive_verbs` re-enters the runtime through an `ApprovalHook` before it shells out (`ConsoleApprovalHook` for the CLI, an inline approval card in the dashboard). A `SelfProtectionPolicy` sits *in front of* approval: calls that target Olympus's own cluster/hosts are hard-denied, not queued.
3. **Append-only audit.** Every tool call is logged twice — once pre-execution (with the approval decision) and once post-execution (with the result).
4. **Bus-based observability.** The orchestrator publishes task/agent/tool/approval/result events to a `Bus` (in-memory default, Redis Streams optional). The dashboard projects this bus into a per-ticket transcript (`TicketStore`) and streams it to the browser over SSE.

These four invariants are the entire safety story. Every component is a different way of arranging them — see [Architecture & concepts](https://docs.01p5.com/guide/architecture).

## Repository layout

Olympus is a monorepo. Full module / endpoint / page reference: **[Codebase map](https://docs.01p5.com/guide/codebase)**.

```
libs/agentlib/      the pure-Python SDK — agent contract, runtime, orchestrator,
                    bus, memory, rollback, MCP, self-protection, budget guard
agents/
  sysadmin/ programmer/ terraform/ ansible/ hpc/   the five routable specialists
  main/             the group-chat coordinator (dispatch + ask_agent)
  terminal_companion/   read-only observer for the in-browser terminal
  dashboard/        stdlib HTTP + SSE API  +  React 18 / Vite / Tailwind SPA
  olympus_cli/      `olympus "..."` terminal entry point
infra/              reference self-host bring-up (Terraform + Ansible + Helm chart)
```

Each agent is its own `pip install -e`-able package. The contract and the rationale behind the bus + group-chat model are in the [agent contract](https://docs.01p5.com/reference/agent-spec) and [design notes](https://docs.01p5.com/reference/design-notes).

## The intelligence layer

Four cooperating features turn Olympus from "agents that run tools" into "a system that learns from prior runs and lets you undo what it did": **memory** retrieval at task start, 👍/👎/correction **feedback** that bends future retrieval, per-verb **rollback** that captures a destructive op's inverse (programmer / sysadmin / terraform), and per-turn/per-agent cost **telemetry** that aggregates across a coordinator's dispatched specialists. All are off by default (`Null*` stores); a deployment opts in by wiring stores through `AgentContext`. Full depth: [Intelligence layer](https://docs.01p5.com/guide/intelligence-layer).

## Model Context Protocol (MCP)

Third-party tools graft onto Olympus over MCP without touching core code. A server is wired with a `target_agent`, a transport (`StdioTransport` or `HttpTransport` for remote Streamable-HTTP servers), and an integrator-supplied destructive allowlist — its tools register *onto that specific agent* (prefixed) and flow through the same `gate_tools` + approval + self-protection machinery as native tools. Declared at startup via `OLYMPUS_MCP_SERVERS` or added at runtime via `POST /mcp/servers`. The production example is **NetDB** (IPAM/DNS/DHCP, ~32 tools over HTTP) grafted onto the sysadmin agent. Worked example + toy server: [MCP integration](https://docs.01p5.com/guide/mcp), [`infra/demo-mcp-server/`](infra/demo-mcp-server/).

## Testing

**~800 backend tests** (pytest) + **393 frontend** (vitest), each gated at 80% coverage on every push (`.github/workflows/ci.yml`), plus an opt-in **23-test Playwright E2E** against a real cluster. How to run each suite — and the live-backend opt-in flags — is in [Development & testing](https://docs.01p5.com/guide/development).

## Further reading

📖 All long-form documentation lives at **[docs.01p5.com](https://docs.01p5.com)** — the single source of truth (project intro, guided quick-start, and every configuration option):

- [Architecture & concepts](https://docs.01p5.com/guide/architecture) — how it fits together + the four safety invariants.
- [Codebase map](https://docs.01p5.com/guide/codebase) — every SDK module, agent, API endpoint, and dashboard page.
- [Agent contract (`AgentSpec`)](https://docs.01p5.com/reference/agent-spec) — the contract every agent implements.
- [Design notes](https://docs.01p5.com/reference/design-notes) — the group-chat / coordinator model and why the bus looks the way it does.
- [Intelligence layer](https://docs.01p5.com/guide/intelligence-layer) — memory + feedback + rollback + telemetry depth doc.
- [MCP integration](https://docs.01p5.com/guide/mcp) — walkthrough + worked example.
- [The live deployment](https://docs.01p5.com/guide/live-deployment) — runbook for the live AWS deployment.
- [Guided walkthrough](https://docs.01p5.com/guide/walkthrough) — a follow-along feature tour.

In-repo: [`PROJECT_PLAN.md`](PROJECT_PLAN.md) — the 10-week course plan, threat model, and what shipped.

## AI usage & attribution

Per the CS 153 AI policy, this project was built with heavy use of AI tools, disclosed here.

- **[@clawdyyy](https://github.com/clawdyyy)** is my dedicated AI account. Both the development assistant (Claude, via Claude Code) and the automation/evaluation agent (openclaw) act through it — so commits, PRs, and automated runs attributed to `@clawdyyy` are AI-authored work I directed and reviewed. I made the design decisions, set the scope, and reviewed every change before it landed.
- The architecture, design decisions, and direction are mine. AI was a tool I used throughout — drafting and refactoring code, fleshing out docs and tests, and assisting with the live deployment and end-to-end testing — but every change was scoped, reviewed, and integrated by me.

## Prior work & attribution

- **[Artemis](https://github.com/artemis-sysadmin/artemis)** — referenced for architectural patterns and prior art (see [`PROJECT_PLAN.md`](PROJECT_PLAN.md)). Artemis is a **group project I built with Thomason Zhao last quarter**; Olympus draws on ideas from that work but is a separate, from-scratch implementation for this course. No Artemis (or other third-party) code was forked into this repository.

## License

Academic / personal — built for [CS 153: Frontier Systems](https://cs153.stanford.edu/) at Stanford University.
