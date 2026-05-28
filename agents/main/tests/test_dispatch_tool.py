"""make_dispatch_tool — round-trip with an injected dispatcher + error
handling. No LLM, no orchestrator.
"""
from __future__ import annotations

from main_agent.agent import make_dispatch_tool


def test_dispatch_tool_round_trip():
    calls = []

    def dispatcher(agent_name: str, subtask: str) -> str:
        calls.append((agent_name, subtask))
        return f"{agent_name} did: {subtask}"

    tool = make_dispatch_tool(dispatcher=dispatcher)
    out = tool.invoke({"agent_name": "sysadmin", "subtask": "list pods"})

    assert out == "sysadmin did: list pods"
    assert calls == [("sysadmin", "list pods")]
    assert tool.name == "dispatch"


def test_dispatch_tool_swallows_dispatcher_error():
    def boom(agent_name: str, subtask: str) -> str:
        raise RuntimeError("agent unreachable")

    tool = make_dispatch_tool(dispatcher=boom)
    out = tool.invoke({"agent_name": "hpc", "subtask": "status"})

    assert "dispatch error" in out
    assert "RuntimeError" in out
    assert "agent unreachable" in out
