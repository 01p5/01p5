"""
Agent registry + orchestrator wiring for the W3-4 multi-agent CLI.

Single source of truth for "which agents does Olympus ship with". Used
by the CLI entry point and by tests that need to construct an
Orchestrator with the real agent set.

Constructing each agent imports its package — and the agent packages
import ``StructuralAgent`` from agentlib, which requires the langchain
1.0 stack. To support tests that run without that stack we accept a
custom ``agents`` list in ``build_orchestrator``.
"""
from __future__ import annotations

from typing import Optional, Sequence

from agentlib import (
    AgentContext,
    AgentSpec,
    InMemoryBus,
    LLMRouter,
    ManualRouter,
    Orchestrator,
    Router,
)


def default_agents() -> list[AgentSpec]:
    """Instantiate the four W3-4 production agents.

    Imports happen here, not at module load, so callers that don't need
    the full agent set (or don't have the LLM stack installed) can avoid
    paying the import cost.
    """
    from ansible_agent.agent import AnsibleAgent
    from programmer.agent import ProgrammerAgent
    from sysadmin.agent import SysadminAgent
    from terraform.agent import TerraformAgent

    return [
        SysadminAgent(),
        ProgrammerAgent(),
        TerraformAgent(),
        AnsibleAgent(),
    ]


def build_orchestrator(
    ctx: AgentContext,
    agents: Optional[Sequence[AgentSpec]] = None,
    router: Optional[Router] = None,
    bus: Optional[InMemoryBus] = None,
) -> Orchestrator:
    """Construct an Orchestrator wired to a fresh in-memory bus by default.

    ``agents``: defaults to ``default_agents()`` (the four production
    agents). Pass an explicit list when testing without the LLM stack.

    ``router``: defaults to ``LLMRouter`` over the agent name → domain
    map. Pass ``ManualRouter`` (or any other ``Router``) for deterministic
    routing in tests.
    """
    if agents is None:
        agents = default_agents()
    bus = bus or InMemoryBus()
    if router is None:
        router = LLMRouter({a.name: a.domain for a in agents})
    return Orchestrator(bus=bus, agents=agents, ctx=ctx, router=router)


# Convenient mapping for ``--router=manual`` users — keyword → agent name.
# Hand-tuned, not a substitute for the LLMRouter; the CLI flag exists so
# tests and offline runs have a deterministic option.
KEYWORD_HINTS: dict[str, str] = {
    "kubectl": "sysadmin",
    "pod": "sysadmin",
    "node": "sysadmin",
    "log": "sysadmin",
    "namespace": "sysadmin",
    "dockerfile": "programmer",
    "compose": "programmer",
    "helm": "programmer",
    "chart": "programmer",
    "terraform": "terraform",
    "infrastructure": "terraform",
    "tf ": "terraform",
    "playbook": "ansible",
    "inventory": "ansible",
    "ansible": "ansible",
}


def manual_router(default: str = "sysadmin") -> ManualRouter:
    """Deterministic router using ``KEYWORD_HINTS``. Useful offline."""
    return ManualRouter(default=default, by_keyword=KEYWORD_HINTS)
