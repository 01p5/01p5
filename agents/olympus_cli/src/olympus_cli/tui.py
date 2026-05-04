"""
Minimal Textual TUI for Olympus.

Decision (W3-4): the terminal UI is built on **textual**, not plain rich.
Reasoning: textual gives us an event loop + widget model that maps
cleanly onto the bus's progress messages, and the same screens scale
into the W5-6 "watch agents collaborate" view.

This is the smallest useful app — submit a NL task, watch streamed
progress messages, surface approval prompts inline. Polished chrome,
multi-task tabs, and the audit-log viewer come in W5-6.

Run:
    olympus-tui                # uses LLMRouter
    olympus-tui --router manual

textual is an OPTIONAL dependency; importing this module without it
raises a clear error. The CLI entry point handles the missing-dep case.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import uuid

try:
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import Footer, Header, Input, Log
except ImportError as exc:  # pragma: no cover — exercised at runtime, not in tests
    raise ImportError(
        "olympus_cli.tui requires the optional 'textual' dependency. "
        "Install with `pip install textual`."
    ) from exc

from agentlib import (
    AgentContext,
    BusMessage,
    InMemoryBus,
    JsonlAuditLogger,
    TaskMessage,
)

from .registry import build_orchestrator, default_agents, manual_router


class OlympusApp(App[int]):
    """Single-screen TUI: input box at top, message log below."""

    CSS = """
    Screen { layout: vertical; }
    #log { height: 1fr; border: round $primary; padding: 0 1; }
    Input { dock: top; }
    """

    def __init__(self, router_name: str = "llm", audit_log_path: str | None = None):
        super().__init__()
        self._router_name = router_name
        self._audit_log_path = audit_log_path or os.path.expanduser("~/.olympus/audit.jsonl")
        self._log: Log | None = None
        self._bus = InMemoryBus()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Input(placeholder="Describe a DevOps task and press Enter…", id="task-input"),
            Log(id="log", highlight=True),
        )
        yield Footer()

    async def on_mount(self) -> None:
        self._log = self.query_one("#log", Log)
        # Subscribe a broadcast sink so every bus message lands in the
        # log — the same contract the audit trail uses.
        self._bus.subscribe("*", self._on_bus_message)
        self._log.write_line("Olympus ready. Type a task and press Enter.")

    def _on_bus_message(self, msg: BusMessage) -> None:
        if self._log is None:
            return
        self._log.write_line(
            f"[{msg.kind}] {msg.sender} → {msg.recipient}: {self._format_payload(msg)}"
        )

    @staticmethod
    def _format_payload(msg: BusMessage) -> str:
        payload = msg.payload
        if isinstance(payload, TaskMessage):
            return f"task {payload.task_id}: {payload.natural_language!r}"
        if hasattr(payload, "summary"):
            return f"result {getattr(payload, 'status', '?')}: {payload.summary!r}"
        return repr(payload)[:200]

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        # Approval prompts are delivered through the inline Log via the
        # ConsoleApprovalHook for v1; the W5-6 milestone replaces this
        # with a modal "approve / reject" widget.
        from agentlib import ConsoleApprovalHook

        ctx = AgentContext(
            approval=ConsoleApprovalHook(),
            audit=JsonlAuditLogger(self._audit_log_path),
        )
        agents = default_agents()
        router = manual_router() if self._router_name == "manual" else None
        orch = build_orchestrator(ctx=ctx, agents=agents, router=router, bus=self._bus)
        task = TaskMessage(task_id=str(uuid.uuid4()), natural_language=text)

        # Run the orchestrator in a worker thread so the UI loop stays
        # responsive. The bus sink already streams progress to the log.
        await asyncio.get_running_loop().run_in_executor(None, orch.run, task)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="olympus-tui")
    parser.add_argument("--router", choices=("llm", "manual"), default="llm")
    parser.add_argument(
        "--audit-log",
        default=os.path.expanduser("~/.olympus/audit.jsonl"),
    )
    args = parser.parse_args(argv)
    app = OlympusApp(router_name=args.router, audit_log_path=args.audit_log)
    return app.run() or 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
