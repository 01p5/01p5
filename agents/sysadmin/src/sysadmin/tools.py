"""
kubectl tool wrappers for the Sysadmin agent.

Each tool shells out to the local kubectl binary. We trust kubectl's own
auth/RBAC for cluster access — the agent runtime layers tool-gating and
human approval on top.

Read-only by default. The single destructive tool, ``delete_pod``, is
listed in ``SysadminAgent.destructive_verbs`` so the runtime forces it
through the approval hook.
"""
from __future__ import annotations

import shlex
import subprocess
from typing import Optional

from langchain_core.tools import tool


_KUBECTL_TIMEOUT_SECONDS = 30


def _run_kubectl(args: list[str]) -> str:
    """Run kubectl and return combined stdout/stderr.

    We surface the raw output to the LLM so it can react to errors
    (missing context, RBAC denial, no such resource) rather than
    swallowing them.
    """
    cmd = ["kubectl", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_KUBECTL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return "ERROR: kubectl not found on PATH"
    except subprocess.TimeoutExpired:
        return f"ERROR: kubectl timeout after {_KUBECTL_TIMEOUT_SECONDS}s for: {shlex.join(cmd)}"
    out = proc.stdout
    if proc.returncode != 0:
        return f"EXIT={proc.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{proc.stderr}"
    return out


@tool
def get_pods(namespace: str = "default") -> str:
    """List pods in a namespace, with status and restart counts."""
    return _run_kubectl(["get", "pods", "-n", namespace, "-o", "wide"])


@tool
def describe_pod(name: str, namespace: str = "default") -> str:
    """Describe a single pod: events, conditions, container state."""
    return _run_kubectl(["describe", "pod", name, "-n", namespace])


@tool
def get_logs(
    pod: str,
    namespace: str = "default",
    container: Optional[str] = None,
    tail_lines: int = 100,
) -> str:
    """Fetch the last N log lines from a pod (optionally a specific container)."""
    args = ["logs", pod, "-n", namespace, f"--tail={int(tail_lines)}"]
    if container:
        args += ["-c", container]
    return _run_kubectl(args)


@tool
def get_events(namespace: str = "default") -> str:
    """List recent events in a namespace, sorted by timestamp."""
    return _run_kubectl(
        ["get", "events", "-n", namespace, "--sort-by=.lastTimestamp"]
    )


@tool
def get_nodes() -> str:
    """List cluster nodes with status and resource usage."""
    return _run_kubectl(["get", "nodes", "-o", "wide"])


@tool
def delete_pod(name: str, namespace: str = "default") -> str:
    """Delete a pod (the controller will recreate it). DESTRUCTIVE — gated by approval."""
    return _run_kubectl(["delete", "pod", name, "-n", namespace])


@tool
def apply_manifest(yaml: str, namespace: str = "default") -> str:
    """Apply a Kubernetes manifest from the supplied YAML string.

    DESTRUCTIVE — gated by approval. The primary use is as the rollback
    inverse for ``delete_pod``: the snapshot captured the pod's spec
    via ``kubectl get pod -o yaml`` before deletion, and this tool
    re-creates the same resource by piping that YAML to
    ``kubectl apply -f -``.

    Standalone use is also fine — any well-formed manifest YAML will
    apply against the named namespace. The agent should prefer
    referencing existing resources over hand-rolling manifests, since
    a hand-rolled manifest defeats the audit chain.
    """
    if not yaml or not yaml.strip():
        return "ERROR: empty manifest YAML"
    cmd = ["kubectl", "apply", "-n", namespace, "-f", "-"]
    try:
        proc = subprocess.run(
            cmd,
            input=yaml,
            capture_output=True,
            text=True,
            timeout=_KUBECTL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return "ERROR: kubectl not found on PATH"
    except subprocess.TimeoutExpired:
        return f"ERROR: kubectl timeout after {_KUBECTL_TIMEOUT_SECONDS}s for: {shlex.join(cmd)}"
    out = proc.stdout
    if proc.returncode != 0:
        return f"EXIT={proc.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{proc.stderr}"
    return out


# ---------------------------------------------------------------------
# Rollback snapshots
# ---------------------------------------------------------------------
#
# Captured AFTER approval, BEFORE the forward kubectl fires, so the
# pre-state snapshot reflects what's about to be destroyed. Returned
# RollbackPlan is persisted by the runtime; executing the rollback
# re-routes through gate_tools → ApprovalHook so the user re-approves
# the undo before the inverse kubectl runs.

def _snapshot_delete_pod(args: dict):
    """Inverse of delete_pod: capture the pod's manifest now, replay
    it via apply_manifest on rollback.

    Strip server-managed fields (``status``, ``metadata.uid``,
    ``metadata.resourceVersion``, etc.) so the re-applied manifest
    doesn't fight the API server on creation. We keep ``spec`` +
    ``metadata.{name, namespace, labels, annotations}`` — enough to
    recreate the pod with the same identity. The dropped fields will
    be repopulated by the controller / API server on apply."""
    from agentlib import RollbackPlan

    name = args["name"]
    namespace = args.get("namespace", "default")

    proc = subprocess.run(
        ["kubectl", "get", "pod", name, "-n", namespace, "-o", "yaml"],
        capture_output=True, text=True, timeout=_KUBECTL_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        # Snapshot failed — return an opt-out plan with empty inverse
        # args. The runtime will record this; the user can see the
        # rollback wasn't capturable when they look at the panel.
        return RollbackPlan(
            inverse_tool="apply_manifest",
            inverse_args={"yaml": "", "namespace": namespace},
            description=(
                f"NO-OP rollback: pre-delete snapshot of {name} in {namespace} "
                f"failed (kubectl exit={proc.returncode}). Pod may already be gone."
            ),
            snapshot={"captured": False, "stderr": proc.stderr[:500]},
        )

    cleaned = _scrub_server_fields(proc.stdout)
    return RollbackPlan(
        inverse_tool="apply_manifest",
        inverse_args={"yaml": cleaned, "namespace": namespace},
        description=f"recreate pod {name} in {namespace} from pre-delete manifest",
        snapshot={"captured": True, "bytes": len(cleaned)},
    )


def _scrub_server_fields(pod_yaml: str) -> str:
    """Remove server-managed metadata + status from a pod manifest
    so it re-applies cleanly. Plain string-level parser — the
    agentlib core stays yaml-dep-free.

    Two scopes of "skip":
      - top-level (e.g. ``status:``): drop the key and everything
        nested under it until the next top-level key.
      - inside ``metadata:`` (e.g. ``uid:``, ``managedFields:``):
        drop the key and any deeper-indented continuation, but
        resume on the next sibling key at indent 2.
    """
    skip_top_level: set[str] = {"status"}
    skip_metadata_subkeys: set[str] = {
        "uid", "resourceVersion", "selfLink", "creationTimestamp",
        "generation", "managedFields", "ownerReferences",
    }

    out: list[str] = []
    in_skip_top = False           # inside a top-level skip section
    in_metadata = False           # inside metadata: block
    in_skip_meta_subkey = False   # inside a metadata subkey we're dropping

    for line in pod_yaml.splitlines(keepends=True):
        stripped = line.lstrip()
        indent = len(line) - len(stripped) if stripped.strip() else 0

        # Top-level keys (indent 0) reset all nested skip state.
        if indent == 0 and stripped.strip() and not stripped.startswith("#"):
            key = stripped.split(":", 1)[0].strip()
            in_skip_top = key in skip_top_level
            in_metadata = (key == "metadata")
            in_skip_meta_subkey = False
            if not in_skip_top:
                out.append(line)
            continue

        # Inside a top-level skip section (e.g. status): drop everything
        # at deeper indent until the next top-level key resets us.
        if in_skip_top:
            continue

        # Inside metadata block.
        if in_metadata and indent == 2:
            # Sibling key — decide fresh whether to skip this subtree.
            key = stripped.split(":", 1)[0].strip()
            in_skip_meta_subkey = key in skip_metadata_subkeys
            if not in_skip_meta_subkey:
                out.append(line)
            continue
        if in_metadata and indent > 2 and in_skip_meta_subkey:
            continue

        out.append(line)
    return "".join(out)


READ_ONLY_TOOLS = [get_pods, describe_pod, get_logs, get_events, get_nodes]
DESTRUCTIVE_TOOLS = [delete_pod, apply_manifest]
ALL_TOOLS = READ_ONLY_TOOLS + DESTRUCTIVE_TOOLS

ROLLBACK_SNAPSHOTS = {
    "delete_pod": _snapshot_delete_pod,
}
