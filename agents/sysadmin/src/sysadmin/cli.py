"""
Minimal CLI for invoking the Sysadmin agent.

Not the production terminal UI (that's W3-4 with textual). This exists
so we can exercise the AgentSpec contract from the shell during W1-2.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict

from agentlib import AgentContext, ConsoleApprovalHook, JsonlAuditLogger, TaskMessage

from .agent import SysadminAgent


def main() -> int:
    parser = argparse.ArgumentParser(prog="olympus-sysadmin")
    parser.add_argument("request", help="Natural-language task for the agent")
    parser.add_argument(
        "--audit-log",
        default=os.path.expanduser("~/.olympus/audit.jsonl"),
        help="Path to the append-only audit log",
    )
    args = parser.parse_args()

    ctx = AgentContext(
        approval=ConsoleApprovalHook(),
        audit=JsonlAuditLogger(args.audit_log),
    )
    task = TaskMessage(task_id=str(uuid.uuid4()), natural_language=args.request)

    agent = SysadminAgent()
    result = agent.handle(task, ctx)
    print(json.dumps(asdict(result), indent=2, default=str))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
