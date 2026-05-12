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
| 9 | Browser E2E suite (5 tests via headless Chromium) | ✅ All 5 pass in ~46s — see below. |

## E2E browser tests

The dashboard UI is also covered by an opt-in Playwright suite that
drives a real headless Chromium against the live deployment. The
tests click through the actual buttons a human would touch, not the
HTTP API.

```bash
# On the dev VM (or any host with playwright + chromium installed):
pip install --user playwright
playwright install --with-deps chromium

cd agents/dashboard
OLYMPUS_LIVE_E2E=1 KUBECONFIG=$HOME/.kube/config \
    pytest tests/test_dashboard_e2e.py -v
```

| Test | What it exercises |
|------|-------------------|
| `test_index_loads_and_health_connects` | Page renders, title is set, health pill flips to "connected" after the first `/healthz` round-trip. |
| `test_submit_task_via_form_lands_in_events_feed` | Type a task with a unique marker, press Enter, watch the `[task]`-kind row appear in the live SSE feed. |
| `test_destructive_task_surfaces_approval_card_and_approve_deletes_pod` | Spawn a throwaway nginx pod, ask the agent to delete it via the form, wait for the approval card to render, click **Approve** in the browser, verify the pod is actually gone via kubectl. |
| `test_destructive_task_reject_preserves_pod` | Same flow but click **Reject** — verify the pod is still alive 10s later. |
| `test_audit_log_panel_renders_recent_calls` | After running tools, the audit panel's polling fills with `.audit-row` entries. |

The destructive tests need `kubectl` configured against the cluster
(`KUBECONFIG=~/.kube/config` works on the dev VM). They create
short-lived `e2e-target-<rand>` pods labelled `e2e-target=true` and
clean up after themselves, so a leaked pod from a failed run can be
swept with:

```bash
kubectl delete pod -l e2e-target=true --grace-period=0 --force
```

The browser's `window.prompt()` (which the dashboard uses for the
approve/reject reason) is auto-accepted by a Playwright `page.on("dialog", ...)`
handler so the tests don't hang on the modal.

Skipped by default unless `OLYMPUS_LIVE_E2E=1` is set, so CI does
not try to spin up Chromium.

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
