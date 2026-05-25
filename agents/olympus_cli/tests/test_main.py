"""Coverage for olympus_cli.main — argv parsing + orchestrator wiring.

We patch build_orchestrator + default_agents so we don't pay the full
agent-import cost (which transitively loads langchain), and so the
returned Orchestrator is a stub that returns canned AgentResults.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pytest
from agentlib import AgentResult

from olympus_cli import main as main_mod


def _orch_returning(status: str) -> MagicMock:
    o = MagicMock()
    o.run.return_value = AgentResult(task_id="t", status=status, summary=f"{status} summary")
    return o


def test_main_success_exits_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["olympus", "list pods", "--audit-log", str(tmp_path / "a.jsonl")])
    buf = io.StringIO()
    with patch("olympus_cli.main.default_agents", return_value=[]), \
         patch("olympus_cli.main.build_orchestrator", return_value=_orch_returning("success")), \
         redirect_stdout(buf):
        rc = main_mod.main()
    assert rc == 0
    body = json.loads(buf.getvalue())
    assert body["status"] == "success"


def test_main_failure_exits_one(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["olympus", "x", "--audit-log", str(tmp_path / "a.jsonl")])
    with patch("olympus_cli.main.default_agents", return_value=[]), \
         patch("olympus_cli.main.build_orchestrator", return_value=_orch_returning("failed")):
        assert main_mod.main() == 1


def test_main_router_manual_uses_manual_router(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv",
                        ["olympus", "x", "--router", "manual",
                         "--audit-log", str(tmp_path / "a.jsonl")])
    with patch("olympus_cli.main.default_agents", return_value=[]), \
         patch("olympus_cli.main.build_orchestrator", return_value=_orch_returning("success")) as bo, \
         patch("olympus_cli.main.manual_router", return_value="MANUAL") as mr:
        main_mod.main()
    mr.assert_called_once()
    # manual_router's return value must reach the orchestrator constructor.
    assert bo.call_args.kwargs["router"] == "MANUAL"


def test_main_router_llm_does_not_call_manual(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["olympus", "x", "--audit-log", str(tmp_path / "a.jsonl")])
    with patch("olympus_cli.main.default_agents", return_value=[]), \
         patch("olympus_cli.main.build_orchestrator", return_value=_orch_returning("success")) as bo, \
         patch("olympus_cli.main.manual_router") as mr:
        main_mod.main()
    mr.assert_not_called()
    # llm mode passes router=None and lets build_orchestrator pick LLMRouter.
    assert bo.call_args.kwargs["router"] is None


def test_main_missing_request_arg(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["olympus"])
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2
