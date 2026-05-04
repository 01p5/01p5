# Olympus Infra

Three layers, applied in order:

| Layer | Path | What it provisions | Run order |
|-------|------|--------------------|-----------|
| 1. State bootstrap | [`aws-bootstrap/`](aws-bootstrap/) | S3 state bucket + DynamoDB lock + scoped IAM role for the Terraform agent. | Once per AWS account, by a privileged human. |
| 2. Cluster | [`terraform/`](terraform/) | A real K8s control-plane VM + worker fleet, on AWS or Proxmox, with Cloudflare DNS. Ported from a working Artemis deployment and rebranded for Olympus. | Once per environment. |
| 3. Workloads | [`ansible/`](ansible/) → [`k8s/charts/olympus/`](k8s/charts/olympus/) | Bring up kubeadm + CNI + Docker on the master, then optionally `helm install` the Olympus dashboard. | After the cluster is reachable. |

`env.sh.template` carries every variable each layer needs. Copy to
`env.sh`, fill in, `source env.sh` before running anything.

## Per-layer detail

### 1. `aws-bootstrap/` — one-time state setup

Same purpose as in W3-4: create the `olympus-tfstate-<account-id>`
bucket, the lock table, and the `olympus_terraform` role the agent
assumes. Apply with a privileged identity, then point everything else
at the resulting bucket via `-backend-config`.

### 2. `terraform/` — cluster provisioning

Two backends; pick one with `TF_VAR_provider_target` (in `env.sh`) or
`-var provider_target=…`:

| `provider_target` | Module | Provisions |
|-------------------|--------|------------|
| `"aws"` (default) | `terraform/aws/` | VPC + public subnet + private cluster subnet, master EC2, worker EC2 fleet (`for_each` over `var.workers`), a small router VM that NATs LAN→WAN and terminates Wireguard, Cloudflare A records for the master. |
| `"pve"`           | `terraform/pve/` | Proxmox VMs for master + workers on a Linux bridge, cloud-init via the `bpg/proxmox` provider, Cloudflare A records pointed at `var.pve_service_ip`. |

`main.tf` instantiates exactly one module via `count`, so the inactive
backend is never evaluated — switching backends costs only a re-init.
Both modules accept the same superset of variables; per-provider
fields are simply ignored by the other backend.

Resource names are tagged `olympus-${var.customer_name}-…` (AWS) or
`k8s-${var.customer_name}-…` (PVE), so multiple deployments coexist
in the same account.

### 3. `ansible/` — host configuration

`master.yml` and `workers.yml` install kubeadm/containerd, init the
control plane with Calico + local-path provisioner, join workers, and
install Helm on the master. The actual Olympus deploy lives in a
final commented-out play in `master.yml` — uncomment after cluster
verification:

```yaml
helm upgrade --install olympus {{ olympus_chart_path }} \
  --set image.repository={{ deployment_registry_host }}/olympus/dashboard \
  --set image.tag={{ olympus_image_tag | default('dev') }}
```

The `helm install` step references `infra/k8s/charts/olympus`, the
chart we built in W5-6.

## Standing up a new account

```bash
cp infra/env.sh.template infra/env.sh
# fill in: cloudflare token, customer name, registry, provider keys
source infra/env.sh

# 1. State bootstrap (one-time, privileged identity).
cd infra/aws-bootstrap
terraform init
terraform apply -var "state_bucket_name=olympus-tfstate-<account-id>"

# 2. Cluster provision.
cd ../terraform
terraform init \
  -backend-config="bucket=olympus-tfstate-<account-id>" \
  -backend-config="dynamodb_table=olympus-tf-locks"
# Pick a backend (or set TF_VAR_provider_target in env.sh):
#   AWS: terraform apply
#   PVE: terraform apply -var provider_target=pve
terraform apply

# 3. Host config.
cd ../ansible
ansible-playbook -i ../terraform/deployment/inventory.ini master.yml
ansible-playbook -i ../terraform/deployment/inventory.ini workers.yml
# (optionally) uncomment + re-run the Olympus deploy block in master.yml
```

## What we expect to hit (and want to surface early)

- **State backend bootstrap is chicken-and-egg** — the state bucket
  cannot itself live in remote state on first apply; `aws-bootstrap`
  is local-state by design.
- **IAM scope drift.** Every new resource an agent stack adds may
  require a policy update; resist `s3:*`/`*` and surface expansions
  in PRs so reviewers catch privilege creep.
- **AWS rate limits during destroy** when the lifecycle policy on
  managed buckets races `terraform destroy`. Either disable the
  lifecycle in tear-down or expect retries.
- **Cloudflare zone ownership.** The DNS module assumes a single
  zone; multi-zone deployments need a per-customer override.

## Layout history

The W3-4 placeholder `infra/sandbox-bucket/` was removed once the real
`infra/terraform/` module landed — no point in two `tf apply` targets
for the same purpose. `aws-bootstrap` stays because it is *prerequisite*
to the real module, not redundant with it.
