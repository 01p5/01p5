"""Olympus main agent — the generalist coordinator of a group-chat ticket.

Unlike the specialist agents, the main agent owns no domain tools. It
converses with the human and collaborates with specialists through two
orchestration tools injected at runtime:

  - ``dispatch(agent_name, subtask)`` — delegate a subtask to a named
    specialist sub-agent and get its result summary back. Built from the
    ``dispatcher`` seam on ``AgentContext`` (wired by the orchestrator).
  - ``ask_agent(target_agent, question)`` — ask a participant a direct
    question (agentlib's directed-Q&A primitive). Built from the
    ``agent_resolver`` seam.

Both seams are optional: with neither wired (e.g. the bare CLI) the main
agent simply replies conversationally.
"""
from .agent import Dispatcher, MainAgent, MainResponse, make_dispatch_tool

__all__ = ["MainAgent", "MainResponse", "Dispatcher", "make_dispatch_tool"]
