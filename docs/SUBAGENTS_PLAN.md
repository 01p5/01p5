# Olympus — Group-Chat Tickets, Sub-Agents & Signals

> Working plan / ground truth. Built collaboratively; reflects the design
> decisions as of this doc. Phase 1 is self-contained and ships value alone.

## The model in one paragraph

A **ticket is a group chat**, built on the existing ChatPage — not a new
surface. The human, a generalist **main agent**, and any **sub-agents** the
main agent spawns are all *participants* posting into one thread. Each
agent's working **context is isolated** (its own per-ticket checkpoint
thread); nobody reads a shared context pool. Agents collaborate by
**asking each other direct questions** (`ask_agent`), and the asked agent
answers from *its own* context. The group-chat transcript is the
human-visible + audited layer. When a ticket is **closed**, its context is
**summarized** and the important details are written to long-term memory;
the per-ticket checkpoints are then discarded.

## Decisions (locked)

- **No whole-context reads.** There is no `read_ticket_context` dump tool.
  Collaboration is directed Q&A via `ask_agent(target, question)`. This
  keeps every agent's context-window lean and preserves isolation.
- **Group chat, not dispatch-and-wait.** The existing chat interface *is*
  the ticket. Sub-agents post into the same thread as distinct actors. No
  separate `/tickets` endpoint, no separate "supervisor mode" screen.
- **Isolated contexts.** Each agent's private context = its per-ticket
  StructuralAgent checkpoint thread. `ask_agent` re-invokes the target
  against that thread — so B answers A from B's retained context without
  anyone seeing a shared pool.
- **Lifecycle → memory.** open → active → closed → **summarized** → important
  details written to the existing `MemoryStore`; per-ticket checkpoints
  discarded on close.
- **`ticket_id` falls back to `task_id`** for standalone (router) tasks, so
  the existing router path keeps working unchanged.
- **Router coexists.** The cheap `LLMRouter` single-domain fast-path stays;
  the main agent is for ambiguous/multi-domain/conversational work.
- **Serial dispatch first.** Parallel sub-agents are a later optimization.

## Key mechanisms (grounded in current code)

- **Bus is the spine.** `agentlib/bus.py` already does typed pub/sub with an
  append-only `log`. The ticket transcript is a *projection* of the bus,
  re-keyed by `ticket_id`. The `"*"` subscriber pattern (used by audit
  sinks today) is how the TicketStore listens.
- **Checkpoint = isolated context.** `StructuralAgent._get_checkpoint_config`
  → `thread_id = f"{task_id}:{agent_type}:{agent_id}"` (main.py:200).
  Re-key to ticket scope and share one checkpointer per (ticket, agent) so
  context survives across `ask_agent` re-invocations within a ticket.
- **Audit log = early ticket log.** `JsonlAuditLogger.log_tool_call`
  (runtime.py:327) already persists per `task_id`. The TicketStore is the
  same idea re-keyed by ticket; keep them separate in P1, reconcile later.
- **SSE already streams the bus** (`GET /events`, `GET /tasks/{id}/events`).
  Group chat = the same stream filtered by `ticket_id`.

---

## Phase 1 — Ticket spine + isolated contexts + `ask_agent`
*(foundation; no routing/UX change yet — ships shared-Q&A + ticket timeline)*

1. **`libs/agentlib/ticket.py`** (new)
   - `TicketEvent` dataclass: `ticket_id`, `seq` (monotonic per ticket),
     `event_id`, `ts`, `actor` (`"human"` | `"main"` | agent name | mcp
     server name), `kind` (`human_message` | `agent_message` | `dispatch` |
     `agent_result` | `tool_call` | `approval_request` | `approval_decision`
     | `mcp_event`), `payload`, `causation_id`, `task_id?`.
   - `TicketStore` Protocol; `JsonlTicketStore` (one JSONL per ticket under
     a `tickets/` dir + in-mem index) and `InMemoryTicketStore` (tests).
   - `transcript(ticket_id, after_seq=0)` → ordered events (the group-chat
     view + audit view).
2. **Thread `ticket_id`** through `bus.new_message`/`BusMessage` and
   `spec.TaskMessage` as optional; default `ticket_id = task_id`.
3. **TicketStore as a bus sink**: subscribe `"*"`, translate each
   `BusMessage` → `TicketEvent`, append. Bus stays source-of-truth.
   - Tool calls go through `ctx.audit`, not the bus → add optional
     `ticket_store` to `AgentContext`; `gate_tools` emits a `tool_call`
     event when present. Leave `JsonlAuditLogger` untouched for now.
4. **Per-ticket checkpoints**: give `StructuralAgent` a ticket-scoped
   `thread_id` and let the orchestrator hand each (ticket, agent) a shared
   checkpointer so an agent's context persists across invocations within
   the ticket.
5. **`ask_agent(target_agent, question)` tool**: re-invokes the target
   against its per-ticket checkpoint + the question; returns the target's
   answer (and logs both as `agent_message` events). Injected into every
   agent's gated toolset; read-only, never approval-gated. *Replaces the
   rejected whole-context read tool.*
6. **Tests**: ticket-log projection + seq ordering; `ask_agent` round-trip
   (B answers from retained context); isolation (A sees B's *answer*, never
   B's raw context); cross-ticket isolation.

## Phase 2 — Group chat (main agent + sub-agents as participants)

7. **`agents/main/`** (new): generalist `MainAgent` (gpt55). Knows a bit of
   everything; tools = `dispatch(agent_name, subtask)` + `ask_agent`. No
   specialist tools of its own.
8. **`Orchestrator.dispatch_to(agent_name, task)`**: run one agent's
   `handle()` synchronously, propagating `ticket_id` + `parent_task_id`;
   result posts to the group chat as a `dispatch`+`agent_result` event.
9. **Frontend — ChatPage becomes a group chat**: render multi-actor messages
   (human / main / each sub-agent styled distinctly) from the ticket's SSE
   stream. The existing chat session = the ticket. No new endpoint.
10. **Tests + UI verification**: main agent dispatches two sub-agents; #2
    answers a question to #1 via `ask_agent`; all turns render in the chat.

## Phase 3 — Ticket close + summarize → memory

11. **Close action** (human ends, or main agent marks resolved). On close:
    summarize the transcript, extract important details, write to the
    existing `MemoryStore`; discard the ticket's per-agent checkpoints.
12. **Tests**: close → summary lands in memory; a later ticket retrieves it
    via the existing memory-at-task-start path.

## Phase 4 — MCP push / async signals *(deferred)*

13. Persistent reader thread per MCP client; `notifications/*` → `mcp_event`
    onto the bus → ticket transcript + SSE. Active routing to a live agent
    needs long-lived agents; v1 just makes pushed events visible + audited.

---

## Touch list (quick reference)

| Area | File | Phase |
|------|------|-------|
| Ticket model + store | `libs/agentlib/src/agentlib/ticket.py` (new) | 1 |
| ticket_id threading | `bus.py`, `spec.py` | 1 |
| tool_call → ticket | `runtime.py` (`gate_tools`, `AgentContext`) | 1 |
| per-ticket checkpoints | `main.py` (`StructuralAgent`), `orchestrator.py` | 1 |
| `ask_agent` tool | `agentlib` (new factory) + `gate_tools` injection | 1 |
| Main agent | `agents/main/` (new) | 2 |
| `dispatch_to` | `orchestrator.py` | 2 |
| Group-chat UI | `agents/dashboard/frontend/src/pages/ChatPage.tsx` | 2 |
| Close + summarize | `orchestrator.py` / dashboard + `memory.py` | 3 |
| MCP push | `mcp.py` (reader thread) | 4 |

## Guardrails carried over

- 80% coverage gate (pytest `--cov-fail-under=80`; vitest thresholds). Every
  new package/module ships with tests to stay above it.
- Shared/transcript content (tool + MCP output, other agents' answers) is
  **untrusted data** — agents reason over it, never treat it as
  instructions. Same injection-guard rule already in the agent prompts.
- Destructive sub-agent tools still route through the approval queue +
  audit, unchanged.
