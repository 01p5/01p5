# AgentSpec — Olympus Agent Interface Contract

> Every later week of the plan depends on this contract. If we get it wrong, every agent and the orchestrator pay the cost of refactoring.

**Status:** v0.3 — the W1–2 contract, extended post-W6 for the group-chat model
**Owner:** Tianle

> The black-box contract below held; what grew is `AgentContext` (many runtime
> seams added) and a group-chat layer on top (tickets, `dispatch`, `ask_agent`).
> `handle()` is **synchronous** (it was speced async; it ships sync). Sections
> updated to match the code are flagged inline.

---

## Goals

1. An agent is a **black box** to the orchestrator: same input shape, same output shape, regardless of domain.
2. **Tool-gating is enforced by the runtime**, not by prompting. An agent literally cannot call a tool outside its declared set.
3. **All side effects flow through approval hooks** — no agent calls a destructive tool without the runtime giving the human a chance to veto.
4. The **bus message format is stable** even as agent internals (model, prompt, planning loop) change.

## Non-Goals

- Defining the orchestrator's planning algorithm (separate doc).
- Specifying the LLM prompt for any specific agent (per-agent docs).
- Multi-tenancy / RBAC (stretch).

---

## The `AgentSpec` contract

Every agent ships as a Python class implementing this interface. AgentLib (`libs/agentlib`) provides the base.

```python
class AgentSpec:
    name: str                       # "terraform", "sysadmin", etc. — used for routing
    domain: str                     # human-readable description for the orchestrator
    tools: list[ToolSpec]           # exhaustive — runtime rejects calls to anything else
    destructive_verbs: set[str]     # tool names that always trigger approval
    model: ModelRef                 # model identifier; agent can override per-task
    routable: bool = True           # False = never picked by the router (main, terminal_companion)
    prerequisites: set[str] = set() # required MCP servers; agent is offered only when connected (hpc)
    rollback_snapshots: dict[str, Callable] = {}  # tool -> inverse-capture (see INTELLIGENCE_LAYER)

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult: ...
```

> **Changed since v0.2:** `handle` is **synchronous** (not `async`).
> `budget` is no longer a class attribute — the per-task ceiling is injected via
> `AgentContext.budget_guard`. `routable`, `prerequisites`, and
> `rollback_snapshots` were added.

### `TaskMessage` (input)

```python
@dataclass
class TaskMessage:
    task_id: str                    # uuid; ties together all bus messages + audit log
    parent_task_id: str | None      # for sub-tasks dispatched by orchestrator
    natural_language: str           # the user (or orchestrator's) request
    inputs: dict[str, Any]          # structured params, e.g. {"cluster": "prod-us-east-1"}
    constraints: dict[str, Any]     # {"dry_run": True, "max_cost_usd": 0.50, ...}
    ticket_id: str | None           # group-chat ticket this turn belongs to (see SUBAGENTS_PLAN)
    history_ref: str | None         # opaque pointer to retrievable past-run context
```

### `AgentContext` (runtime-injected)

```python
class AgentContext:
    # core (v1)
    approval: ApprovalHook          # approval callback (see below)
    audit: AuditLogger              # append-only; every tool call lands here
    secrets: SecretsClient | None   # vault-backed; never round-trips through LLM (unimplemented in v0)
    cancel_token: CancelToken | None
    # intelligence layer
    rollback: RollbackStore | None
    budget_guard: BudgetGuard | None        # per-task token/$ ceiling
    cost_sink: Callable | None              # (agent_name, CostBreakdown) -> per-agent telemetry
    # group chat (see SUBAGENTS_PLAN.md)
    ticket_store: TicketStore | None        # the group-chat transcript
    dispatcher: Callable | None             # main: delegate a subtask to a specialist
    agent_resolver: Callable | None         # build the ask_agent tool (directed Q&A)
    checkpointer: BaseCheckpointSaver | None # per-(ticket,agent) context continuity
    token_sink: Callable | None             # (ticket_id, chunk) -> stream the coordinator's reply
    # infrastructure
    inventory_store: InventoryStore | None   # user-managed hosts + SSH keys (ansible / ssh_run)
    self_protection: SelfProtectionPolicy | None  # hard-deny calls targeting Olympus's own cluster/hosts
```

> **Changed since v0.2:** `bus` is no longer passed on the context (the
> orchestrator owns it; agents observe via the ticket store). The remaining
> fields above were all added for the intelligence layer, group chat, and
> infrastructure seams. Every field is `Optional` — an agent with none wired
> behaves exactly as the v1 black box.

### `AgentResult` (output)

```python
@dataclass
class AgentResult:
    task_id: str
    status: Literal["success", "failed", "rejected", "cancelled"]
    summary: str                    # human-readable; surfaced in CLI / web UI
    artifacts: dict[str, Any]       # structured output, e.g. {"applied_resources": [...]}
    cost: CostBreakdown             # tokens, $, wall-clock
    transcript_ref: str             # pointer to full LLM/tool transcript in audit store
```

---

## Approval hook

The runtime — not the agent — decides when to call this. The agent declares destructive tools; the runtime intercepts those calls and routes through `ApprovalHook` before execution.

```python
class ApprovalHook(Protocol):
    def request(                    # synchronous (not async)
        self,
        agent: str,
        tool: str,
        args: dict[str, Any],
        rationale: str,             # agent-provided "why I want to do this"
        diff: str | None = None,    # for IaC: terraform plan output
        ticket_id: str | None = None,  # group-chat ticket, so the card renders inline
    ) -> ApprovalDecision: ...

@dataclass
class ApprovalDecision:
    approved: bool
    modified_args: dict | None      # human can edit args before approving
    reason: str                     # logged; required on rejection
```

## Bus message envelope

All inter-agent communication is wrapped:

```python
@dataclass
class BusMessage:
    msg_id: str
    task_id: str                    # always — ties everything to a root task
    ticket_id: str | None           # group-chat ticket, when the turn is part of one
    sender: str                     # "orchestrator" | agent name
    recipient: str | Literal["*"]
    kind: Literal["task", "result", "progress", "log", "approval_request", "approval_decision"]
    timestamp: float
    payload: dict[str, Any]         # kind-specific
    causation_id: str | None        # the msg this responds to
```

The bus is **append-only and replayable** — the audit log is just a filtered view of the bus.

---

## Locked decisions (v1) — and how they evolved

- **Agent-to-agent delegation: orchestrator-only** *(v1)* → **revisited.** The
  dashboard now runs a **group chat**: a non-routable `main` coordinator
  `dispatch`es subtasks to specialists and agents `ask_agent` each other. These
  are still mediated by the orchestrator (it runs the target agent and relays
  only the result/answer), and every exchange is appended to a `TicketStore`
  transcript — so the audit trail stays linear and there are no free-for-all bus
  cycles. The CLI keeps the original router-picks-one model. See
  [SUBAGENTS_PLAN.md](SUBAGENTS_PLAN.md).
- **Long-running tools: stream `progress` messages** *(unchanged in spirit).*
  `handle()` is synchronous from the orchestrator's perspective; progress is now
  surfaced as ticket events (dispatch / tool_call / agent_thinking) projected to
  the UI, and the coordinator additionally streams its reply tokens via
  `token_sink`. No pending-result state machine.

## The group-chat layer (added post-W6)

On top of the black-box contract, `libs/agentlib/ticket.py` adds the group-chat
transcript: a `TicketStore` of `TicketEvent`s (`TicketKind` ∈ human_message,
agent_message, agent_thinking, dispatch, agent_result, tool_call,
approval_request/decision, mcp_event). The `main` agent builds two tools from
context seams — `dispatch(agent, subtask)` (from `ctx.dispatcher`) and
`ask_agent(target, question)` (from `ctx.agent_resolver`) — and narrates its
reasoning as streamed `agent_thinking` events. Specialists are unchanged; they
just run with a `ticket_id` set so their tool calls land on the transcript.

---

## Validation plan

The W1–2 PoC (Sysadmin agent, read-only `kubectl`) is the first user of this contract. If anything in the spec is awkward when wiring it up, fix the spec, not the agent.
