# Bus backend (v2) — decision

**Status:** Decided W5 (locks the W4 open question).
**Owner:** Tianle.
**Supersedes:** the "decide by W4" line in PROJECT_PLAN.md → Open Questions.

## Decision

**Redis Streams** for v2. In-memory bus stays as the default for tests
and single-process dev. The `BusMessage` envelope and the `subscribe()`
/ `publish()` shape from `agentlib.bus` are preserved; the v2 backend
implements the same Python interface so swapping is a one-line change
in the orchestrator wiring.

## What forced the decision

W3-4 left two cross-cutting requirements that the in-memory bus cannot
meet:

1. **Multi-process / multi-host orchestration.** The W5-6 web dashboard
   is a separate process from the agent worker; both need to read and
   write the same bus.
2. **Replay for the audit log + UI catch-up.** The orchestrator may
   crash mid-run; the dashboard may attach late. The bus must keep
   ordered history, not just live fan-out.

## Why not the alternatives

| Option | Why ruled out |
|--------|---------------|
| Stay in-memory | Fails requirement 1 immediately. Workable only for the CLI; the dashboard is the W5-6 deliverable. |
| NATS / NATS JetStream | Powerful, but adds a separate operational footprint (NATS server) for marginal benefit at our scale (single-digit agents, single-digit tasks/min). Worth re-evaluating in W9-10 if multi-tenant. |
| Postgres LISTEN/NOTIFY + a `messages` table | We'd already need Redis for caches/locks elsewhere; doubling the durable-store surface is a worse trade than just using Redis Streams. |
| Kafka | Operational weight far exceeds the workload. No. |

## What Redis Streams gives us

- **Append-only, ordered, replayable.** `XADD` writes; `XRANGE` /
  `XREAD` reads from any point; consumer groups handle attach-late and
  multi-consumer fan-out.
- **One stream per task** (`task:{task_id}`) keeps the per-task audit
  view trivial; a single broadcast stream (`bus:all`) keeps the "*"
  subscriber semantics.
- **TTL / `MAXLEN`** caps storage growth. The full audit lives in the
  JSONL file the runtime already writes — Redis is the live tier.
- **Tiny operational surface.** A single `redis:7-alpine` container in
  docker-compose is the whole infra.

## What it does NOT solve (and we're OK with)

- **Cross-region.** Redis replication is single-primary. Acceptable —
  the agent is single-region by design.
- **Schema enforcement.** Payloads are still Python dataclasses
  serialized to JSON. We accept the same lack of schema check the
  in-memory bus has; the runtime validates tool args downstream.

## Migration plan

1. Add a `RedisStreamsBus` class to `agentlib.bus` that implements the
   same `subscribe(recipient, callback)` + `publish(BusMessage)` +
   `log` interface.
2. The CLI defaults to `InMemoryBus`; pass `--bus redis://…` to swap.
3. The dashboard backend constructs `RedisStreamsBus` directly.
4. Tests stay on `InMemoryBus`; one integration test covers the Redis
   path against a `redis:7-alpine` container started by docker-compose.

## When to revisit

- If we sustain >50 tasks/minute or need multi-tenant isolation, look
  at NATS JetStream — its consumer-group semantics are nicer at scale.
- If the audit-log JSONL becomes the bottleneck, consider routing the
  log straight to Redis (and dropping the file) instead of writing both.
