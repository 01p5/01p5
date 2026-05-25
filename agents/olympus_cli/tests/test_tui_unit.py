"""Unit tests for olympus_cli.tui.

Covers the synchronous bits — constructor, _format_payload dispatch,
_on_bus_message guard, and main() arg parsing. The textual event loop
itself (app.run()) is mocked rather than driven, because spinning a
real TUI inside pytest is flaky and slow.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# All tests need textual at import time. CI installs it; local devs need
# it too. Skip cleanly if not present so dev boxes without textual
# don't error.
textual = pytest.importorskip("textual")  # noqa: F841

from agentlib import BusMessage, TaskMessage  # noqa: E402

from olympus_cli.tui import OlympusApp, main as tui_main  # noqa: E402


def test_constructor_sets_router_and_default_audit_log():
    app = OlympusApp(router_name="llm")
    assert app._router_name == "llm"
    assert app._audit_log_path.endswith(".olympus/audit.jsonl")


def test_constructor_accepts_custom_audit_log(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    app = OlympusApp(router_name="manual", audit_log_path=path)
    assert app._audit_log_path == path


def test_format_payload_renders_taskmessage_with_id_and_text():
    msg = BusMessage(
        msg_id="m1", task_id="t1", timestamp=0.0,
        sender="orchestrator", recipient="sysadmin", kind="task",
        payload=TaskMessage(task_id="abc-123", natural_language="list pods"),
    )
    out = OlympusApp._format_payload(msg)
    assert "task abc-123" in out
    assert "list pods" in out


def test_format_payload_renders_object_with_summary_attribute():
    result = MagicMock(status="success", summary="all done")
    msg = BusMessage(msg_id="m", task_id="t", timestamp=0.0,
                     sender="x", recipient="y", kind="result", payload=result)
    out = OlympusApp._format_payload(msg)
    assert "success" in out
    assert "all done" in out


def test_format_payload_falls_back_to_repr_for_unknown_payload():
    msg = BusMessage(msg_id="m", task_id="t", timestamp=0.0,
                     sender="a", recipient="b", kind="log",
                     payload={"raw": "string here"})
    out = OlympusApp._format_payload(msg)
    assert "raw" in out


def test_on_bus_message_no_log_attribute_does_nothing():
    """Before on_mount runs, self._log is None — guard must skip."""
    app = OlympusApp(router_name="llm")
    app._log = None
    msg = BusMessage(msg_id="m", task_id="t", timestamp=0.0,
                     sender="x", recipient="y", kind="log", payload="hello")
    app._on_bus_message(msg)  # must not raise


def test_on_bus_message_writes_to_log_when_set():
    app = OlympusApp(router_name="llm")
    fake_log = MagicMock()
    app._log = fake_log
    msg = BusMessage(msg_id="m", task_id="t", timestamp=0.0,
                     sender="orchestrator", recipient="sysadmin", kind="log", payload="hi")
    app._on_bus_message(msg)
    fake_log.write_line.assert_called_once()
    line = fake_log.write_line.call_args.args[0]
    assert "[log]" in line
    assert "orchestrator" in line
    assert "sysadmin" in line


def test_main_constructs_app_with_parsed_args_and_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv",
                        ["olympus-tui", "--router", "manual",
                         "--audit-log", str(tmp_path / "audit.jsonl")])
    fake_app = MagicMock()
    fake_app.run.return_value = 0
    with patch("olympus_cli.tui.OlympusApp", return_value=fake_app) as ctor:
        rc = tui_main()
    assert rc == 0
    fake_app.run.assert_called_once()
    kwargs = ctor.call_args.kwargs
    assert kwargs["router_name"] == "manual"
    assert kwargs["audit_log_path"].endswith("audit.jsonl")


def test_main_returns_zero_even_when_app_run_returns_none():
    """app.run() can return None; main() coerces to 0."""
    fake_app = MagicMock()
    fake_app.run.return_value = None
    with patch("olympus_cli.tui.OlympusApp", return_value=fake_app):
        rc = tui_main([])
    assert rc == 0
