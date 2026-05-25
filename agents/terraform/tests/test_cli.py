"""Coverage for terraform.cli."""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
from agentlib import AgentResult

from terraform import cli


def test_main_success(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["olympus-terraform", "plan the aws stack", "--audit-log", str(tmp_path / "a.jsonl")])
    buf = io.StringIO()
    with patch("terraform.cli.TerraformAgent.handle",
               return_value=AgentResult(task_id="t", status="success", summary="planned")), \
         redirect_stdout(buf):
        rc = cli.main()
    assert rc == 0
    assert json.loads(buf.getvalue())["summary"] == "planned"


def test_main_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["olympus-terraform", "x", "--audit-log", str(tmp_path / "a.jsonl")])
    with patch("terraform.cli.TerraformAgent.handle",
               return_value=AgentResult(task_id="t", status="failed", summary="boom")):
        assert cli.main() == 1


def test_main_no_args(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["olympus-terraform"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
