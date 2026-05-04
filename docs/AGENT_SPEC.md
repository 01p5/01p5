# AgentSpec — Olympus Agent Interface Contract

> Every later week of the plan depends on this contract. If we get it wrong, every agent and the orchestrator pay the cost of refactoring.

**Status:** v0.2 (W1–2, frozen for W3)
**Owner:** Tianle
**Reviewers needed:** self-review against PoC, then frozen for W3

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
    model: ModelRef                 # LiteLLM-routable identifier; agent can override per-task
    budget: BudgetGuard             # token + $ ceiling per task

    async def handle(
        self,
        task: TaskMessage,
        ctx: AgentContext,
    ) -> AgentResult: ...
```

### `TaskMessage` (input)

```python
@dataclass
class TaskMessage:
    task_id: str                    # uuid; ties together all bus messages + audit log
    parent_task_id: str | None      # for sub-tasks dispatched by orchestrator
    natural_language: str           # the user (or orchestrator's) request
    inputs: dict[str, Any]          # structured params, e.g. {"cluster": "prod-us-east-1"}
    constraints: dict[str, Any]     # {"dry_run": True, "max_cost_usd": 0.50, ...}
    history_ref: str | None         # opaque pointer to retrievable past-run context
```

### `AgentContext` (runtime-injected)

```python
class AgentContext:
    bus: BusClient                  # publish/subscribe to shared context bus
    approval: ApprovalHook          # async approval callback (see below)
    secrets: SecretsClient          # vault-backed; never round-trips through LLM
    audit: AuditLogger              # append-only; every tool call lands here
    cancel_token: CancelToken       # cooperative cancellation
```

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
    async def request(
        self,
        agent: str,
        tool: str,
        args: dict[str, Any],
        rationale: str,             # agent-provided "why I want to do this"
        diff: str | None = None,    # for IaC: terraform plan output
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
    sender: str                     # "orchestrator" | agent name
    recipient: str | Literal["*"]
    kind: Literal["task", "result", "progress", "log", "approval_request", "approval_decision"]
    timestamp: float
    payload: dict[str, Any]         # kind-specific
    causation_id: str | None        # the msg this responds to
```

The bus is **append-only and replayable** — the audit log is just a filtered view of the bus.

---

## Locked decisions (v1)

These were the open questions blocking freeze; both are now decided. Revisit when W6 cross-agent workflows force the issue.

- **Agent-to-agent delegation: orchestrator-only.** Agents do not publish `kind="task"` to other agents. The orchestrator is the only sender of tasks. *Why:* keeps the bus a star topology in v1, makes the audit log linear, and avoids cycles before we have a planner that can reason about them. *Revisit after W6* once we have real cross-agent workflows that benefit from direct dispatch.
- **Long-running tools: stream `progress` messages.** `handle()` is synchronous from the orchestrator's perspective but emits `kind="progress"` bus messages while it works. No "pending result + follow-up" state machine in v1. *Why:* a state machine is the more flexible design but doubles the bus surface area; streaming covers Terraform apply / k8s rollout for the foreseeable future.

The Sysadmin PoC and the W3-4 multi-agent rollout both build on these.

---

## Validation plan

The W1–2 PoC (Sysadmin agent, read-only `kubectl`) is the first user of this contract. If anything in the spec is awkward when wiring it up, fix the spec, not the agent.
