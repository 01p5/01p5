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

from langchain.tools import tool


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


READ_ONLY_TOOLS = [get_pods, describe_pod, get_logs, get_events, get_nodes]
DESTRUCTIVE_TOOLS = [delete_pod]
ALL_TOOLS = READ_ONLY_TOOLS + DESTRUCTIVE_TOOLS
