"""Coverage for sysadmin.cli — argparse + main() exit-code paths.

The agent itself is mocked. We only verify the CLI shim: argv parsing,
ctx wiring, the success → 0 / failed → 1 mapping, and json output.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
from agentlib import AgentResult

from sysadmin import cli


def _fake_success(*_args, **_kwargs):
    return AgentResult(task_id="t-1", status="success", summary="ok")


def _fake_failure(*_args, **_kwargs):
    return AgentResult(
        task_id="t-1", status="failed", summary="boom",
    )


def test_main_success_path_exits_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["olympus-sysadmin", "list pods", "--audit-log", str(tmp_path / "a.jsonl")])
    buf = io.StringIO()
    with patch("sysadmin.cli.SysadminAgent.handle", side_effect=_fake_success), redirect_stdout(buf):
        rc = cli.main()
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["status"] == "success"
    assert out["summary"] == "ok"


def test_main_failure_path_exits_one(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["olympus-sysadmin", "list pods", "--audit-log", str(tmp_path / "a.jsonl")])
    with patch("sysadmin.cli.SysadminAgent.handle", side_effect=_fake_failure):
        rc = cli.main()
    assert rc == 1


def test_main_missing_request_arg_argparse_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["olympus-sysadmin"])  # no positional
    with pytest.raises(SystemExit) as exc:
        cli.main()
    # argparse exits 2 on bad CLI args.
    assert exc.value.code == 2
