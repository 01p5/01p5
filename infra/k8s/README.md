# Olympus on Kubernetes (W5-6)

Plan item: *full K8s deploy (extends the W3-4 minimal AWS path).*

This is the smallest meaningful K8s deploy: a single Deployment that
embeds the dashboard + orchestrator + bus + the four agent runtimes,
fronted by a Service (and optional Ingress). It's enough for the
"sysadmin agent verifies its own pod" demo.

## Out-of-scope (W7+)

- Per-agent Deployments + cross-process Redis bus. Single-process is
  simpler and matches the W4 in-memory bus default.
- Horizontal scaling. The orchestrator is single-replica by design
  in v1.
- Cluster autoscaler / node-pool config — depends on the cluster.

## Pre-flight (NOT EXECUTED)

The user's local AWS / kubectl creds may point at company
infrastructure. Per the project rules, *do not* run the commands below
without confirming the target context first.

```bash
# 1. Build the dashboard image (locally or in CI):
docker build -t olympus/dashboard:dev \
  --build-arg INSTALL_LLM_STACK=1 \
  -f Dockerfile .

# 2. Push to whatever registry the cluster pulls from.

# 3. Create the secrets out-of-band:
kubectl create secret generic olympus-secrets \
  --from-literal=anthropic_api_key="$ANTHROPIC_API_KEY" \
  --from-literal=openai_api_key="$OPENAI_API_KEY"

# 4. Install:
helm install olympus infra/k8s/charts/olympus \
  --set image.repository=olympus/dashboard \
  --set image.tag=dev

# 5. Port-forward the dashboard:
kubectl port-forward svc/olympus-olympus 8765:80
# → http://localhost:8765
```

## What the chart provisions

| Resource | Purpose |
|----------|---------|
| `Deployment olympus-olympus` | Dashboard + agents (single replica). |
| `Service olympus-olympus` | Internal entrypoint. |
| `Ingress` | Optional; off by default. |
| `ServiceAccount olympus-agent` | Identity for the Sysadmin agent's in-cluster kubectl. |
| `Role + RoleBinding olympus-olympus-sysadmin` | Read-only on pods/logs/events/nodes. `--set rbac.destructive=true` adds `delete pod`; that path is still gated by the runtime ApprovalHook. |
| `PersistentVolumeClaim olympus-olympus-audit` (opt-in) | Persists the JSONL audit log across pod restarts. |

## Verifying the deploy (smoke test the user runs)

After `helm install` and `port-forward`:

1. `curl http://localhost:8765/healthz` → `{"ok": true}`.
2. Open the dashboard in a browser, submit "list pods in default".
3. The sysadmin agent runs `kubectl get pods -n default` via the
   in-cluster ServiceAccount — *no* pod-mutation verbs unless the
   chart was installed with `rbac.destructive=true`.
4. The audit log (`/audit` endpoint) shows the call.

## Drift expected

- Resource limits in `values.yaml` are starting hypotheses. Calibrate
  in W7-8 alongside the success-metric work.
- The chart deliberately does not provision a Redis service — the
  dashboard uses the in-memory bus by default. When the W7+ workflow
  needs cross-process state, add a `redis` subchart and a
  `--bus redis://…` flag.
