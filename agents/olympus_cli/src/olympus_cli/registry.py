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

import os
from pathlib import Path

from agentlib import (
    AgentContext,
    AgentSpec,
    EmbeddingMemoryStore,
    InMemoryBus,
    JsonlMemoryStore,
    LLMRouter,
    ManualRouter,
    MemoryStore,
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
    memory: Optional[MemoryStore] = None,
) -> Orchestrator:
    """Construct an Orchestrator wired to a fresh in-memory bus by default.

    ``agents``: defaults to ``default_agents()`` (the four production
    agents). Pass an explicit list when testing without the LLM stack.

    ``router``: defaults to ``LLMRouter`` over the agent name → domain
    map. Pass ``ManualRouter`` (or any other ``Router``) for deterministic
    routing in tests.

    ``memory``: optional ``MemoryStore`` so prior runs are retrieved
    at task start. When ``None``, defaults are picked from env vars:
      - ``OLYMPUS_MEMORY=disabled``   → no memory (orchestrator default)
      - ``OLYMPUS_MEMORY=embeddings`` → ``EmbeddingMemoryStore`` (needs
        ``OPENAI_API_KEY``)
      - otherwise                     → ``JsonlMemoryStore`` at
        ``OLYMPUS_MEMORY_PATH`` (default: ``~/.olympus/memory.jsonl``)
    """
    if agents is None:
        agents = default_agents()
    bus = bus or InMemoryBus()
    if router is None:
        router = LLMRouter({a.name: a.domain for a in agents})
    if memory is None:
        memory = _memory_from_env()
    return Orchestrator(
        bus=bus, agents=agents, ctx=ctx, router=router, memory=memory
    )


def _memory_from_env() -> Optional[MemoryStore]:
    """Pick a default memory backend based on environment.

    Returns ``None`` when the user explicitly disables memory — the
    Orchestrator then falls back to its ``NullMemoryStore`` default."""
    mode = os.environ.get("OLYMPUS_MEMORY", "").lower()
    if mode == "disabled":
        return None
    path = os.environ.get(
        "OLYMPUS_MEMORY_PATH",
        str(Path.home() / ".olympus" / "memory.jsonl"),
    )
    if mode == "embeddings":
        # EmbeddingMemoryStore degrades to lexical search if the API
        # key isn't set, so this is safe to construct unconditionally.
        return EmbeddingMemoryStore(path.replace(".jsonl", ".emb.jsonl"))
    return JsonlMemoryStore(path)


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
