"""main agent CLI shim coverage — mirrors the other agents' test_cli.py."""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
from agentlib import AgentResult

from main_agent import cli


def test_main_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        ["olympus-main", "what's broken?", "--audit-log", str(tmp_path / "a.jsonl")],
    )
    buf = io.StringIO()
    with patch("main_agent.cli.MainAgent.handle",
               return_value=AgentResult(task_id="t", status="success", summary="looking into it")), \
         redirect_stdout(buf):
        rc = cli.main()
    assert rc == 0
    assert json.loads(buf.getvalue())["status"] == "success"


def test_main_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        ["olympus-main", "x", "--audit-log", str(tmp_path / "a.jsonl")],
    )
    with patch("main_agent.cli.MainAgent.handle",
               return_value=AgentResult(task_id="t", status="failed", summary="boom")):
        assert cli.main() == 1


def test_main_no_args(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["olympus-main"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
