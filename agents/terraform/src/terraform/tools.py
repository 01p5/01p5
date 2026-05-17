"""
Terraform tool wrappers for the Terraform agent.

Each tool shells out to the local ``terraform`` binary. Plan output is
the IaC equivalent of a diff — the runtime hands it to ``ApprovalHook``
when the agent asks for ``apply`` or ``destroy``.

State awareness in v1 is intentionally minimal: tools accept a working
directory and operate on whatever state the user's backend points at
(local statefile, S3, etc.). We do not implement state surgery
(``terraform state mv/rm``) yet — that's a sharper foot-gun than apply
and deserves its own design.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from typing import Optional

from langchain_core.tools import tool

# Plans against AWS or large modules can take minutes; bias generous.
_TF_TIMEOUT_SECONDS = 600


def _run_terraform(args: list[str], cwd: str) -> str:
    """Run terraform in ``cwd`` and return combined stdout/stderr.

    The agent gets the raw text — exit codes, error messages, prompt
    requests — so it can react instead of swallowing failures.
    """
    if not os.path.isdir(cwd):
        return f"ERROR: working directory {cwd!r} does not exist"
    cmd = ["terraform", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_TF_TIMEOUT_SECONDS,
            # -input=false on every command keeps the LLM from being
            # silently blocked on an interactive prompt.
            env={**os.environ, "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"},
        )
    except FileNotFoundError:
        return "ERROR: terraform not found on PATH"
    except subprocess.TimeoutExpired:
        return f"ERROR: terraform timeout after {_TF_TIMEOUT_SECONDS}s for: {shlex.join(cmd)}"
    out = proc.stdout
    if proc.returncode != 0:
        return f"EXIT={proc.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{proc.stderr}"
    return out


@tool
def tf_init(working_dir: str, upgrade: bool = False) -> str:
    """Initialize a Terraform working directory (download providers, set up backend).

    working_dir: absolute path to the Terraform module/root.
    upgrade: if True, pass `-upgrade` to refresh providers.
    """
    args = ["init", "-no-color"]
    if upgrade:
        args.append("-upgrade")
    return _run_terraform(args, working_dir)


@tool
def tf_validate(working_dir: str) -> str:
    """Validate the configuration in ``working_dir`` (syntax + internal consistency)."""
    return _run_terraform(["validate", "-no-color"], working_dir)


@tool
def tf_plan(working_dir: str, var_file: Optional[str] = None, target: Optional[str] = None) -> str:
    """Compute an execution plan and return its human-readable text.

    The output of this tool is what gets shown to the human as the
    ``diff`` argument to the approval hook before any apply.
    """
    args = ["plan", "-no-color", "-detailed-exitcode"]
    if var_file:
        args += [f"-var-file={var_file}"]
    if target:
        args += [f"-target={target}"]
    return _run_terraform(args, working_dir)


@tool
def tf_show(working_dir: str) -> str:
    """Show the current state in human-readable form (no mutation)."""
    return _run_terraform(["show", "-no-color"], working_dir)


@tool
def tf_output(working_dir: str, name: Optional[str] = None) -> str:
    """Read named output (or all outputs) from current state."""
    args = ["output", "-no-color"]
    if name:
        args.append(name)
    return _run_terraform(args, working_dir)


@tool
def tf_apply(working_dir: str, var_file: Optional[str] = None, target: Optional[str] = None) -> str:
    """Apply configuration. DESTRUCTIVE — gated by approval.

    -auto-approve is set because the human approval already happened
    in the ApprovalHook before this tool was invoked. Letting terraform
    re-prompt would deadlock under TF_INPUT=0.
    """
    args = ["apply", "-no-color", "-auto-approve"]
    if var_file:
        args += [f"-var-file={var_file}"]
    if target:
        args += [f"-target={target}"]
    return _run_terraform(args, working_dir)


@tool
def tf_destroy(working_dir: str, target: Optional[str] = None) -> str:
    """Destroy managed infrastructure. DESTRUCTIVE — gated by approval."""
    args = ["destroy", "-no-color", "-auto-approve"]
    if target:
        args += [f"-target={target}"]
    return _run_terraform(args, working_dir)


@tool
def tf_restore_state(working_dir: str, state_json: str) -> str:
    """Restore Terraform state from a captured JSON snapshot, then
    re-apply to reconcile real resources to match.

    DESTRUCTIVE — gated by approval. Primary use is as the rollback
    inverse for ``tf_apply``: the snapshot captured the prior state
    via ``terraform state pull`` before the apply fired; this tool
    pipes that JSON back via ``terraform state push -`` and then runs
    ``terraform apply`` to bring real resources in line with the
    restored state.

    Caveats — make these visible to the user on the approval card:
      - This is best-effort. State surgery + apply can fail if the
        cloud provider changed in between, or if the prior state
        references resources that no longer exist.
      - The apply step is auto-approved at the terraform CLI level
        because the human approval already happened upstream in
        ApprovalHook.
      - If state restoration fails, the cloud is left in the
        post-apply state; the user can re-attempt or manually
        ``terraform state pull/push`` to recover.
    """
    if not state_json or not state_json.strip():
        return "ERROR: empty state JSON — nothing to restore"
    if not os.path.isdir(working_dir):
        return f"ERROR: working directory {working_dir!r} does not exist"

    push_cmd = ["terraform", "state", "push", "-"]
    try:
        push = subprocess.run(
            push_cmd,
            cwd=working_dir,
            input=state_json,
            capture_output=True,
            text=True,
            timeout=_TF_TIMEOUT_SECONDS,
            env={**os.environ, "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"},
        )
    except FileNotFoundError:
        return "ERROR: terraform not found on PATH"
    except subprocess.TimeoutExpired:
        return f"ERROR: terraform timeout after {_TF_TIMEOUT_SECONDS}s for: {shlex.join(push_cmd)}"
    if push.returncode != 0:
        return (
            f"STATE PUSH FAILED (exit={push.returncode})\n"
            f"STDOUT:\n{push.stdout}\nSTDERR:\n{push.stderr}\n"
            f"Cloud resources NOT touched."
        )

    # State restored; now apply so real resources reconcile.
    apply_out = _run_terraform(["apply", "-no-color", "-auto-approve"], working_dir)
    return f"STATE PUSH OK ({len(state_json)} bytes)\nAPPLY:\n{apply_out}"


# ---------------------------------------------------------------------
# Rollback snapshots
# ---------------------------------------------------------------------
#
# Captured AFTER approval, BEFORE tf_apply runs, so the snapshot
# reflects the pre-apply state. Runtime persists the returned plan;
# executing the rollback re-routes through gate_tools → ApprovalHook.

def _snapshot_tf_apply(args: dict):
    """Inverse of tf_apply: snapshot the prior state file via
    ``terraform state pull``; restoration runs tf_restore_state
    against the captured JSON.

    On failure to capture (no state, terraform not on PATH, etc.)
    return a flagged no-op plan — the user sees in the rollback panel
    that the entry is non-executable. Better an honest "we tried"
    than a silent missing-rollback case."""
    from agentlib import RollbackPlan

    working_dir = args["working_dir"]
    proc = subprocess.run(
        ["terraform", "state", "pull"],
        cwd=working_dir,
        capture_output=True,
        text=True,
        timeout=_TF_TIMEOUT_SECONDS,
        env={**os.environ, "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"},
    )

    if proc.returncode != 0 or not proc.stdout.strip():
        return RollbackPlan(
            inverse_tool="tf_restore_state",
            inverse_args={"working_dir": working_dir, "state_json": ""},
            description=(
                f"NO-OP rollback: pre-apply state pull from {working_dir!r} "
                f"failed (exit={proc.returncode}). This typically means no "
                f"state existed yet (first apply); the rollback would be "
                f"`terraform destroy` against this dir, which is destructive."
            ),
            snapshot={"captured": False, "stderr": proc.stderr[:500]},
        )

    return RollbackPlan(
        inverse_tool="tf_restore_state",
        inverse_args={"working_dir": working_dir, "state_json": proc.stdout},
        description=(
            f"restore terraform state for {working_dir!r} from pre-apply snapshot, "
            f"then `terraform apply` to reconcile real resources"
        ),
        snapshot={"captured": True, "bytes": len(proc.stdout)},
    )


READ_ONLY_TOOLS = [tf_init, tf_validate, tf_plan, tf_show, tf_output]
DESTRUCTIVE_TOOLS = [tf_apply, tf_destroy, tf_restore_state]
ALL_TOOLS = READ_ONLY_TOOLS + DESTRUCTIVE_TOOLS

ROLLBACK_SNAPSHOTS = {
    "tf_apply": _snapshot_tf_apply,
}
