"""Minimal CLI for the main agent — same shape as the other agents'.

Without an orchestrator wiring the ``dispatcher`` + ``agent_resolver``
seams, the main agent has no dispatch/ask tools and will simply reply
conversationally. The CLI is mostly useful for shape checks and dev; the
real entry point is the dashboard's group-chat ticket.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict

from agentlib import AgentContext, ConsoleApprovalHook, JsonlAuditLogger, TaskMessage

from .agent import MainAgent


def main() -> int:
    parser = argparse.ArgumentParser(prog="olympus-main")
    parser.add_argument("request", help="Natural-language message for the main agent")
    parser.add_argument(
        "--audit-log",
        default=os.path.expanduser("~/.olympus/audit.jsonl"),
    )
    args = parser.parse_args()

    ctx = AgentContext(
        approval=ConsoleApprovalHook(),
        audit=JsonlAuditLogger(args.audit_log),
    )
    task = TaskMessage(task_id=str(uuid.uuid4()), natural_language=args.request)

    agent = MainAgent()
    result = agent.handle(task, ctx)
    print(json.dumps(asdict(result), indent=2, default=str))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
