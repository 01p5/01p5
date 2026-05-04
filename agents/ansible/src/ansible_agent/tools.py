"""
Ansible tool wrappers for the Ansible agent.

The destructive surface is ``run_playbook`` and ``run_module`` — anything
that mutates remote hosts. The dry-run path (``check_playbook``) and
inventory inspection are read-only.

We don't shell out via ``ansible-vault`` here; secrets come from
``AgentContext.secrets`` (vault-backed) and never round-trip through the
LLM.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from typing import Optional

from langchain_core.tools import tool

# Playbook runs against fleets can be slow.
_ANSIBLE_TIMEOUT_SECONDS = 900


def _run(cmd: list[str], cwd: Optional[str] = None) -> str:
    """Run an ansible-family binary; return combined stdout/stderr."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_ANSIBLE_TIMEOUT_SECONDS,
            env={**os.environ, "ANSIBLE_FORCE_COLOR": "0"},
        )
    except FileNotFoundError:
        return f"ERROR: {cmd[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return f"ERROR: ansible timeout after {_ANSIBLE_TIMEOUT_SECONDS}s for: {shlex.join(cmd)}"
    out = proc.stdout
    if proc.returncode != 0:
        return f"EXIT={proc.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{proc.stderr}"
    return out


@tool
def list_inventory(inventory: str) -> str:
    """List hosts/groups in an Ansible inventory (file or directory)."""
    return _run(["ansible-inventory", "-i", inventory, "--list"])


@tool
def graph_inventory(inventory: str) -> str:
    """Render the inventory as a host/group graph for human inspection."""
    return _run(["ansible-inventory", "-i", inventory, "--graph"])


@tool
def check_playbook(
    playbook: str,
    inventory: str,
    limit: Optional[str] = None,
    extra_vars: Optional[dict[str, str]] = None,
) -> str:
    """Dry-run a playbook (--check). No state changes; output is the diff
    fed to the approval hook before run_playbook is invoked."""
    cmd = ["ansible-playbook", playbook, "-i", inventory, "--check", "--diff"]
    if limit:
        cmd += ["--limit", limit]
    if extra_vars:
        for k, v in extra_vars.items():
            cmd += ["-e", f"{k}={v}"]
    return _run(cmd)


@tool
def run_playbook(
    playbook: str,
    inventory: str,
    limit: Optional[str] = None,
    extra_vars: Optional[dict[str, str]] = None,
) -> str:
    """Execute a playbook against the inventory. DESTRUCTIVE — gated by approval."""
    cmd = ["ansible-playbook", playbook, "-i", inventory]
    if limit:
        cmd += ["--limit", limit]
    if extra_vars:
        for k, v in extra_vars.items():
            cmd += ["-e", f"{k}={v}"]
    return _run(cmd)


@tool
def run_module(
    inventory: str,
    pattern: str,
    module: str,
    args: Optional[str] = None,
) -> str:
    """Run an ad-hoc module (`ansible -m`). DESTRUCTIVE — gated by approval.

    Even read-ish modules (setup, ping) go through approval because
    we cannot statically tell whether ``module=command`` will rm -rf.
    """
    cmd = ["ansible", pattern, "-i", inventory, "-m", module]
    if args:
        cmd += ["-a", args]
    return _run(cmd)


READ_ONLY_TOOLS = [list_inventory, graph_inventory, check_playbook]
DESTRUCTIVE_TOOLS = [run_playbook, run_module]
ALL_TOOLS = READ_ONLY_TOOLS + DESTRUCTIVE_TOOLS
