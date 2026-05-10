# Olympus — Live Deploy Reference

The W5–6 deliverable is running on a real Proxmox-backed Kubernetes
cluster. This file is the runbook: how to reach it, what's been
exercised against it, and what remains rough.

## Where everything lives

| Layer | Location | Notes |
|-------|----------|-------|
| PVE host | `root@10.0.10.249` | Throwaway intranet box. SSH key auth. |
| Cluster nodes | `10.0.10.20` (master) + `.21/.22/.23` (workers) | All Ubuntu 22.04, kubeadm v1.30.14, Calico CNI. SSH as `k8s` with `infra/terraform/deployment/k8s.pem`. |
| Dev workstation VM | `unics@10.0.10.30` (`olympus-dev`) | 8 vCPU / 16 GB / 120 GB. Mirror of the original dev box; full toolchain installed; `~/.kube/config` is the cluster admin.conf. |
| Dashboard (cluster) | `http://<any-node>:30093` (NodePort) | E.g. `http://10.0.10.20:30093/healthz`. |
| Dashboard (proxied) | `http://10.0.10.30/` | Caddy on the dev VM front-ends the NodePort — no port to remember. |

## Driving the live system

```bash
# Health
curl http://10.0.10.30/healthz                 # → {"ok": true}

# Submit a task
curl -s -X POST http://10.0.10.30/tasks \
     -H 'Content-Type: application/json' \
     -d '{"natural_language": "list pods in default namespace"}'
# → {"task_id": "..."}

# Poll
curl -s http://10.0.10.30/tasks/<task_id> | jq

# Watch the live event stream (SSE)
curl -N http://10.0.10.30/events

# Pending approvals
curl -s http://10.0.10.30/approvals | jq

# Resolve an approval
curl -s -X POST http://10.0.10.30/approvals/<approval_id> \
     -H 'Content-Type: application/json' \
     -d '{"approved": true,  "reason": "ok"}'

# Audit log (JSONL)
curl -s http://10.0.10.30/audit
```

The browser UI at `http://10.0.10.30/` shows live events, the
approval queue, and the audit log — all auto-refreshing.

## What's been exercised against the live system

Every check below ran end-to-end against the real cluster. ✅ = passed.

| # | Check | Result |
|---|-------|--------|
| 1 | Single read-only task (`list pods`) | ✅ ~12s round-trip; structured `SysadminResponse`. |
| 2 | Destructive flow with approval | ✅ Spawned an `nginx-test` deployment; agent invoked `delete_pod`; approval card surfaced; approving via API fired the actual `kubectl delete`; ReplicaSet recreated the pod. |
| 3 | Destructive flow with rejection | ✅ Same task, rejected via API; agent honored the rejection; pod was untouched. |
| 4 | Self-diagnosis (Olympus reads its own pod) | ✅ Agent chained `get_pods → describe_pod → get_events → get_logs` and produced a full report. **Found a real bug in our own code** (see below). |
| 5 | 5 concurrent tasks | ✅ All 5 finished in ~21s wall clock. No race conditions. Each task_id mapped to its own result. |
| 6 | Audit log integrity | ✅ Every destructive verb has `approved=True/False` (never `None`). JSONL parses cleanly. Timestamps not strictly monotonic under concurrency — see Known Issues. |
| 7 | Crash recovery | ✅ Force-killed the pod mid-task; new pod up + `/healthz` ok in **~4s**. In-flight tasks are lost (in-memory bus + emptyDir). |
| 8 | External access | ✅ Caddy on the dev VM proxies `http://10.0.10.30/` → cluster NodePort. |

## Bug found by the live system, fixed in the live system

Self-diagnosis surfaced this in its own log tail:

```
AttributeError: 'str' object has no attribute 'get'
File "agentlib/main.py", line 259, in _calculate_response_cost
    content_dict.get("type", "") == "web_search_call"
```

`AIMessage.content` can be either a plain string (chat-completions
API path) or a list of content blocks (Responses API path). The cost
calculator was iterating a string and treating each character as a
dict. Caught + logged so non-fatal, but spammy.

Fixed in `libs/agentlib/src/agentlib/main.py`: guard with
`isinstance(content, list)` and `isinstance(content_dict, dict)`
before calling `.get`. After rebuild + reship + rollout, fresh pod
logs show **0 AttributeErrors**.

The agent diagnosed its own bug, then we shipped the fix. That is
exactly the loop Olympus is supposed to enable.

## Known issues / limits

- **Cluster-scoped resources are RBAC-forbidden.** The chart's `Role`
  is namespace-scoped (`default`), so `kubectl get nodes` returns
  Forbidden. Stress test surfaced this — agents handled it
  gracefully and reported the RBAC denial in their summaries. Fix:
  promote to `ClusterRole` + `ClusterRoleBinding` for `nodes` (and
  any other cluster-scoped resources we add).

- **In-flight tasks die with the pod.** Bus is in-memory inside the
  dashboard pod; the audit log lives on `emptyDir`. A pod restart
  loses both. The Redis bus (`agentlib.RedisStreamsBus`) is already
  designed and tested with `fakeredis`; wiring it into the chart +
  adding a `redis` subchart is the W7 fix. The audit-log PVC
  (`audit.persistence.enabled`) is opt-in for the same reason.

- **Audit-log timestamps under concurrency are not strictly
  monotonic.** Multiple worker threads `open(..., "a")` the same
  file without locking. OS-level append is atomic for short records
  so no records are lost, but order can be slightly inverted. Real
  fix would be a single writer thread + queue.

- **Image distribution is local-tar / `ctr import`.** No registry on
  the cluster; image tags pin via `image.pullPolicy=Never`. Fine
  for a single dev setup. Multi-machine or rolling updates need a
  cluster-internal registry (the `daemon.json` insecure-registry
  config the master playbook already writes was meant for this — the
  registry itself is not deployed).

- **`output_version="responses/v1"` was tried and reverted** — OpenAI
  GPT-5+ enforces strict tool schemas through it, and the langchain
  schema serializer drops `additionalProperties: false`. We work
  around this by passing tool schemas as dicts (with our own strict
  flag) through `runtime._strict_schema_dict` — see
  `libs/agentlib/src/agentlib/runtime.py`. Watch out if upgrading
  langchain-openai.

## What it costs to run

Quick observation across this session: a "list pods" task is roughly
3-4 OpenAI calls (gpt-5-mini), tool result included; the
self-diagnosis task chained 4 tools across ~22 calls. At gpt-5-mini
pricing this is well under a cent per task. Telemetry isn't measuring
this yet — wired up in `_calculate_response_cost` but the
`agent_execution_context` accumulator isn't surfaced through the
dashboard's task result. W7 plan item.

## Tear-down (if needed)

```bash
# Stop the dashboard
helm uninstall olympus

# Stop the cluster (keeps the VMs, just kills kubeadm)
ansible-playbook -i infra/terraform/deployment/inventory.ini \
   <reset-playbook>      # not yet written

# Tear down the VMs (will destroy the cluster)
cd infra/terraform && terraform destroy -var provider_target=pve
```

The PVE host stays around either way — it's the user's intranet box.
