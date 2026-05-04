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

from langchain.tools import tool

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


READ_ONLY_TOOLS = [tf_init, tf_validate, tf_plan, tf_show, tf_output]
DESTRUCTIVE_TOOLS = [tf_apply, tf_destroy]
ALL_TOOLS = READ_ONLY_TOOLS + DESTRUCTIVE_TOOLS
