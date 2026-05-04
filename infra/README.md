# Olympus Infra

W3-4 plan item: *minimal AWS deploy path (one agent against real infra)
— pull forward to surface IAM/state/secrets pain early.*

This directory holds the smallest set of Terraform stacks the Olympus
Terraform agent needs in order to perform a real apply against AWS.

| Stack | Purpose | Apply with |
|-------|---------|------------|
| `aws-bootstrap/` | Per-account, one-time. Creates the state bucket, lock table, and the IAM role the agent assumes. | A privileged human, once per AWS account. |
| `sandbox-bucket/` | The smoke target. The agent applies + destroys this stack to prove the loop. | `olympus-terraform "apply infra/sandbox-bucket"` (after bootstrap). |

## How the deploy path works

1. **Identity.** A privileged human runs `aws-bootstrap` once. It
   creates `olympus_terraform`, an IAM role with a *scoped* policy:
   state-bucket access + `s3:*` on `olympus-sandbox-*`. Nothing else.
2. **State.** Both stacks use the S3 backend created by the bootstrap.
   State is versioned and SSE-AES256 encrypted; locking is via DynamoDB
   (`olympus-tf-locks`). The sandbox stack also opts into S3-native
   `use_lockfile = true` so we exercise the newer locking path.
3. **Secrets.** The agent never sees AWS credentials directly. It runs
   under an instance profile / OIDC role / `aws sso` session that
   resolves to `olympus_terraform`. `AgentContext.secrets` (vault-backed
   in v2) is the path for *application* secrets the agent must inject
   into resources.
4. **Approval.** Every `tf_apply` and `tf_destroy` goes through the
   runtime's `ApprovalHook` with the most recent `tf_plan` output as
   the diff. Console hook for solo runs; webhook hook for ops-on-call.
5. **Audit.** `JsonlAuditLogger` records every tool call (including
   `terraform plan`) with task ID, agent, args, result, and approval
   decision. The bus log preserves message ordering.

## Bootstrapping a new account

```bash
# Run once with a privileged identity (e.g. your own SSO admin role).
cd infra/aws-bootstrap
terraform init
terraform apply -var "state_bucket_name=olympus-tfstate-<account-id>"
```

After bootstrap completes, configure the sandbox stack's backend:

```bash
cd ../sandbox-bucket
terraform init \
  -backend-config="bucket=olympus-tfstate-<account-id>" \
  -backend-config="dynamodb_table=olympus-tf-locks"
```

Then hand control to the agent:

```bash
olympus-terraform \
  "Apply infra/sandbox-bucket with name_suffix=$(uuidgen | head -c 6)"
```

## Pain we expect to hit (and want to surface early)

- **State backend bootstrap is chicken-and-egg.** The state bucket
  cannot itself live in remote state on first apply — that's why
  `aws-bootstrap` is local-state by design.
- **IAM scope drift.** Every new resource an agent stack adds requires
  a policy update. The temptation is `s3:*` / `*`. Resist; surface
  expansions in PRs so reviewers see the privilege creep.
- **Plan diffs are noisy.** Terraform's plan output is verbose; the
  agent's job is to *summarize* it for the approval prompt, not pipe
  the whole thing through. See `TerraformResponse.plan_summary`.
- **AWS rate limits during destroy.** Sandbox lifecycle expires
  objects after 7 days, which often races with `terraform destroy`.
  Either disable the lifecycle in tear-down or expect retries.
