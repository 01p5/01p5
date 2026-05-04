"""
``olympus`` CLI entry point — top-level orchestrator runner.

Loads all four production agents, builds the orchestrator + router,
takes a NL task on the command line, dispatches, prints the structured
result. CLI uses ConsoleApprovalHook + JsonlAuditLogger by default;
``--router=manual`` swaps the LLM router out for deterministic keyword
routing (useful offline).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict

from agentlib import AgentContext, ConsoleApprovalHook, JsonlAuditLogger, TaskMessage

from .registry import build_orchestrator, default_agents, manual_router


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="olympus",
        description="Olympus orchestrator — route a NL task to the right agent.",
    )
    parser.add_argument("request", help="Natural-language task")
    parser.add_argument(
        "--router",
        choices=("llm", "manual"),
        default="llm",
        help="Router to use (default: llm). 'manual' is deterministic and offline.",
    )
    parser.add_argument(
        "--audit-log",
        default=os.path.expanduser("~/.olympus/audit.jsonl"),
        help="Path to the append-only audit log",
    )
    args = parser.parse_args(argv)

    ctx = AgentContext(
        approval=ConsoleApprovalHook(),
        audit=JsonlAuditLogger(args.audit_log),
    )

    agents = default_agents()
    router = manual_router() if args.router == "manual" else None
    orch = build_orchestrator(ctx=ctx, agents=agents, router=router)

    task = TaskMessage(task_id=str(uuid.uuid4()), natural_language=args.request)
    result = orch.run(task)
    print(json.dumps(asdict(result), indent=2, default=str))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
