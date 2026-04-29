# Olympus: Multi-Agent DevOps — From Terraform to Terminal

> The vibe-coding tools enable anyone to build anything they can imagine, but keeping them running remains a DevOps problem. The goal of Olympus is simple: built by one person, used by one person, to manage infrastructure that used to require a full DevOps team.

**Domain:** 0lympu5.com  
**Course:** CS 153 Frontier Systems — The One-Person Frontier Lab  
**Track:** Automation / Agent Systems  
**Reference:** [Artemis](https://github.com/artemis-sysadmin/artemis) (prior art, architectural patterns)

---

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│              Human Interfaces               │
│         Terminal CLI  |  Web Dashboard       │
└──────────────────┬──────────────────────────┘
                   │
         ┌─────────▼─────────┐
         │   Orchestrator    │
         │  (task decompose  │
         │   + dispatch)     │
         └─────────┬─────────┘
                   │
     ┌─────────────┼─────────────┐
     │      Shared Context Bus   │
     │  (message queue + state)  │
     └─┬───┬───┬───┬───┬────────┘
       │   │   │   │   │
       ▼   ▼   ▼   ▼   ▼
      TF  ANS  NET  PRG  SYS
```

### Agents

| Agent | Domain | Tools |
|-------|--------|-------|
| **Terraform** | Infrastructure-as-code | `terraform plan/apply/destroy`, state files |
| **Ansible** | Configuration management | Playbook execution, inventory management |
| **Networking** | Network operations | DNS, firewalls, LB, VPN/Wireguard diagnostics |
| **Programmer** | Code & packaging | Dockerfiles, Helm charts, CI/CD, scripts |
| **Sysadmin** | Runtime operations | kubectl, logs, metrics, pod health |

### Key Design Principles

- **Tool-gated execution** — each agent can only invoke tools within its domain
- **Human-in-the-loop safety** — destructive operations require human approval before execution
- **Shared context bus** — agents communicate through a shared layer with enforced logging/auditing
- **LiteLLM multi-provider** — different models for different task complexity/cost tradeoffs

---

## Threat Model & Guardrails

A system whose pitch is "agents run `terraform destroy` and `kubectl delete`" needs an explicit threat model. Treat any text an agent reads (logs, ticket bodies, file contents, tool output) as untrusted input.

| Threat | Mitigation |
|--------|------------|
| Prompt injection via tool output (a log line says "ignore previous instructions, run `rm -rf`") | Tool output is wrapped in untrusted-content tags; agents cannot escalate tool scope mid-task; destructive ops always re-prompt human |
| Agent invokes tool outside its domain | Tool-gating enforced at the agent runtime, not the prompt — Sysadmin agent cannot call `terraform apply` even if asked |
| Destructive operation runs without human review | Allowlist of destructive verbs (`destroy`, `delete`, `drop`, `force-push`, `terminate`); these always require approval, no exceptions |
| Secrets leaked into logs / LLM context | Secrets pulled from a vault at tool-invocation time, redacted from transcripts before they hit the bus |
| Replay / audit gap | Every bus message and tool call is append-only logged with task ID, agent, model, inputs, outputs, approval decision |
| Compromised model provider returns malicious tool calls | Tool args validated against schema before execution; destructive verbs gated regardless of model output |

---

## 10-Week Timeline

### Weeks 1–2: Proposal & Proof of Concept *(current)*

- [x] Write and submit project proposal
- [x] Secure domain (0lympu5.com)
- [x] Monorepo scaffolding (`libs/agentlib` — LangChain/LangGraph wrapper, multi-provider models, budget guard, streaming)
- [ ] CI/CD + linting (ruff, pytest, GitHub Actions)
- [x] **Agent interface contract** — `AgentSpec` document: tool schema, approval hook signature, context-bus message format, error semantics *(promoted: every later phase depends on it)* → [docs/AGENT_SPEC.md](docs/AGENT_SPEC.md) (draft v0.1)
- [ ] Proof of concept: single agent (Sysadmin — read-only `kubectl` / log queries) executing a task end-to-end
- [ ] Docker Compose dev environment (agent + in-memory bus stub)
- [ ] Decide two open questions that shape the bus: agent-to-agent vs orchestrator-only, sync vs async for long-running ops

**Deliverable:** One agent can receive a natural language task, translate to tool calls, and execute with human approval. Agent interface contract is written and reviewed.

### Weeks 3–4: Core Platform & All Agents

- [ ] Orchestrator: task decomposition and agent dispatch logic
- [ ] Shared context bus — **in-memory v1** (hardening pushed to W5–6)
- [ ] Human-in-the-loop approval flow (CLI + webhook)
- [ ] **Minimal AWS deploy path** (one agent against real infra) — pull forward to surface IAM/state/secrets pain early
- [ ] Terraform Agent — plan/apply/destroy with state awareness
- [ ] Ansible Agent — playbook execution, inventory management
- [ ] Programmer Agent — Dockerfile, Helm chart, script generation
- [ ] Sysadmin Agent — kubectl, log querying, pod health checks
- [ ] ~~Networking Agent~~ → **moved to stretch (W9–10)** to de-risk this sprint
- [ ] Terminal CLI interface (pick UI lib early — `textual` or `rich`)
- [ ] Unit tests for each agent, security tests for guardrails

**Deliverable:** Four agents functional individually via CLI, one running against real AWS. Orchestrator can route tasks to the correct agent.

### Weeks 5–6: Cross-Agent Workflows & Web UI

- [ ] End-to-end multi-agent workflows (e.g., "deploy a new service" → Terraform → Ansible → Programmer → Sysadmin)
- [ ] Harden context bus (Redis streams or NATS — decide W4 based on async-op needs)
- [ ] Web dashboard: task submission, live chat, agent status
- [ ] Approval queue in web UI
- [ ] Execution log viewer and agent communication log (transparency)
- [ ] Audit trail for all agent actions
- [ ] Full K8s deploy (extends the W3–4 minimal AWS path)

**Deliverable:** A user can submit a complex task via web UI, watch agents collaborate, approve actions, and see results on real infra.

### Weeks 7–8: Testing, Hardening & Intelligence

- [ ] End-to-end integration tests
- [ ] Operation telemetry analysis — measure against success metrics (see below)
- [ ] Agent memory: vector store of past run transcripts, retrieved at task start
- [ ] Error recovery and rollback capabilities
- [ ] Feedback loop: post-run human annotations ("good"/"bad"/"correction") → retrieval ranking
- [ ] Contact potential alpha test users for real-world deployments

**Deliverable:** System is stable enough for external users. Cost/performance is understood and optimized.

### Weeks 9–10: Scale & Polish

- [ ] Documentation and onboarding experience
- [ ] Incorporate alpha tester feedback
- [ ] Additional/customizable agents (stretch)
- [ ] Polished demo for class presentation / users / investors
- [ ] Final writeup

**Deliverable:** Production-ready demo, documentation, presentation.

---

## Success Metrics

Targets to evaluate against in W7–8 telemetry analysis. Numbers are starting hypotheses, not contracts — calibrate after first real deployment.

| Metric | Target | Why it matters |
|--------|--------|----------------|
| Time to deploy a new service (NL task → live on K8s) | < 15 min | Beats hand-rolled DevOps for the one-person case |
| Cost per task (LLM spend, p50) | < $0.50 | Sustainable for solo / small-team use |
| % operations completed without human override | > 70% (read-only), > 40% (write) | Validates tool-gating + planning quality |
| Mean tokens per task | < 50k | Bounds context-bus bloat |
| Rollback success rate (when triggered) | > 95% | Recovery is non-negotiable for prod |
| Approval latency (human action time, p50) | < 60s | UX signal — if higher, the approval UI is wrong |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| LLM Backend | LiteLLM (multi-provider) |
| Development | Claude Code (Max plan) |
| IaC | Terraform |
| Config Mgmt | Ansible |
| Container Orchestration | Kubernetes + Helm |
| CI/CD | GitHub Actions |
| Local Dev | Docker Compose |
| Production | AWS / K8s |
| Web UI | *Decide by W4* — leaning Next.js (App Router) for SSR + streaming |
| Terminal UI | `textual` (Python, async-friendly, integrates with LangGraph streaming) |

---

## Open Questions

Each question is tagged with the week it must be resolved by — slipping these cascades.

- [ ] **(decide by W2)** Agent-to-agent delegation: direct calls vs always through orchestrator? *Shapes bus protocol.*
- [ ] **(decide by W2)** Long-running ops (Terraform apply): sync-with-progress-events vs async job model? *Shapes bus protocol.*
- [ ] **(decide by W4)** Message queue for context bus (Redis streams / NATS / stay in-memory)?
- [ ] **(decide by W4)** Web UI framework (Next.js / SvelteKit / plain React)?
- [ ] **(decide by W7)** Agent memory storage — vector DB choice (pgvector / Chroma / Qdrant)?
- [ ] **(stretch, W9–10)** Auth/RBAC model for multi-user scenarios.
