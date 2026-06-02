# Olympus — Live Deploy Reference

The live system runs on AWS and is reachable at **<https://demo.0lympu5.com>**.
This file is the runbook: how it's shaped, how to reach it, what's been
exercised against it, and what's still rough.

> The cluster is provisioned and operated from a separate deployment repo
> (Terraform + Ansible + the Olympus Helm chart). The in-repo [`infra/`](../infra/)
> is the reference self-host path; this doc describes the live AWS deployment.
> A clone-and-deploy-it-yourself version lives in the sandbox deployment repo.

## Architecture

```
Cloudflare (zone 0lympu5.com)
  ├─ A   demo.0lympu5.com   → cluster control-plane EIP   (TLS via the in-cluster proxy)
  └─ NS  lab.0lympu5.com    → the persistent NetDB DNS server (delegated)

AWS
  ├─ Kubernetes cluster (kubeadm on EC2, Calico CNI)
  │    └─ Helm release: one Deployment = dashboard + orchestrator + bus +
  │       all agent runtimes (sysadmin/programmer/terraform/ansible/hpc/main)
  │       + optional GPU/Slurm demo dashboards; Service + TLS reverse proxy
  └─ Persistent NetDB / Technitium DNS / Kea server (separate Terraform state,
       survives cluster --fresh) — authoritative for lab.0lympu5.com, grafted
       onto the sysadmin agent over HTTP MCP (~32 tools, locked to the cluster)
```

The chat is a **group chat**: the human talks to the `main` coordinator, which
dispatches to specialists. Auth is enforced — Google OAuth or email OTP — so the
HTTP API below is reachable in a browser session, not anonymously (except
`/healthz`).

## Reaching it

- **Browser:** <https://demo.0lympu5.com> → log in (Google or email OTP) → land on **Chat**.
- **Health (public):**
  ```bash
  curl https://demo.0lympu5.com/healthz        # → {"ok": true}
  ```
- **Authenticated API:** every other endpoint requires a session cookie. The
  browser flow sets it; scripted access needs the same cookie. Key surfaces:
  `POST /tickets/{id}/messages` (drive the coordinator), `GET /tickets/{id}/events`
  (SSE transcript), `GET /tickets/{id}/stream` (live reply tokens),
  `POST /tickets/{id}/close`, `GET /tools` + `POST /tools/{agent}/{tool}` (gated
  direct invocation), `GET /approvals` + `POST /approvals/{id}`, `GET /audit`,
  `GET /telemetry`, `GET/POST /mcp/servers`, `GET/POST /inventory/hosts`,
  `GET /gpu` + `/slurm`, `GET /admin/accounting` (admin only).

## The UI

Nav: **Chat** · **Sessions** · **Auditing** · **Capabilities** (Kubernetes /
Terraform / Ansible / Programmer / HPC) · **Hosts** · **MCP** · **Terminal** ·
**Admin**.

- **Chat** — the group-chat ticket. The coordinator's reply forms in-thread;
  dispatch chips (`main → sysadmin`), tool-call chips, an interleaved 💭 thinking
  trace, and inline approval cards render chronologically. The left rail is your
  past sessions.
- **Capabilities** — per-domain consoles (pod/node tables, Terraform stack
  cards, Ansible playbooks, the Programmer generators, the HPC Slurm/GPU views).
  Destructive actions surface the same approval cards as chat.
- **MCP** — every wired MCP server with status + lazy tool catalog; the NetDB
  integration card shows the DNS/IPAM tools grafted onto sysadmin.
- **Terminal** — in-browser SSH (xterm.js); the terminal companion answers
  questions about the scrollback.
- **Admin** — per-user daily cost caps + spending ledger.

## What's been exercised against the live system

| Check | Result |
|-------|--------|
| Group-chat turn with dispatch | ✅ `main` dispatches to specialists; dispatch chips + tool-call chips + interleaved thinking trace render live. |
| Destructive flow with approval | ✅ Destructive tool surfaces an inline approval card in the transcript; approving fires the real call, rejecting honors the veto. |
| Self-protection | ✅ Calls targeting Olympus's own namespace / nodes (e.g. `shell_exec` against the cluster) are hard-denied before approval — a user cannot escalate by managing the system that gates them. |
| NetDB / DNS over MCP | ✅ Sysadmin gains ~32 NetDB tools; an agent-created record resolves publicly under `lab.0lympu5.com` (`dig test.lab.0lympu5.com`). NetDB's `:8080` is locked to the cluster (no anonymous access). |
| Sub-agent cost accounting | ✅ A coordinator turn's recorded cost includes the specialists it dispatched; the telemetry footer + Admin ledger reflect the full turn, with a per-agent breakdown. |
| Auth | ✅ Google OAuth + email OTP gate the dashboard; domain allowlist + admin role enforced. |
| TLS + crash recovery | ✅ HTTPS at `demo.0lympu5.com`; a pod restart brings `/healthz` back in seconds (in-flight ticket state is lost — see below). |
| Persistent DNS survives redeploy | ✅ The NetDB/Technitium server has its own Terraform state, so a cluster `--fresh` leaves the zone + IPAM data intact. |

## Known issues / limits

- **The ticket store is in-memory.** Chat transcripts live in the dashboard pod
  (`InMemoryTicketStore`), so a redeploy / pod restart clears existing
  conversations. A file-backed store exists in `ticket.py`; wiring it (or Redis)
  is the persistence fix.
- **In-flight work dies with the pod.** The bus is in-memory; the audit log is on
  an opt-in PVC. A restart loses in-flight tasks. `RedisStreamsBus` is built +
  tested; wiring it + a Redis subchart is the durability path.
- **Self-protection is config-driven.** It reads the cluster namespace (downward
  API) + an explicit node list (`OLYMPUS_SELF_NODES` / chart `hardening.selfNodes`).
  A node that isn't listed isn't protected — keep the list in sync with the fleet.
- **MCP push-notifications (Phase 4) not wired.** MCP servers are registered at
  startup / via `POST /mcp/servers`; live server-initiated tool-list changes
  aren't yet consumed.

## Deploy / teardown

The cluster + dashboard are deployed from the separate deployment repo
(`./inf/deploy.sh` → Terraform apply + Ansible + Helm; `--ansible-only` re-rolls
the app without touching infra; `--fresh` rebuilds the cluster). The persistent
NetDB/DNS server is brought up once with `./inf/deploy.sh netdb-up` and is *not*
torn down by a cluster `--fresh`. See that repo for the full operator guide; the
in-repo [`infra/`](../infra/) mirrors the same shape for self-hosting.
