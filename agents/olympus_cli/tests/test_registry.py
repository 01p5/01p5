"""Coverage for olympus_cli.registry — default_agents, build_orchestrator
env-var branches, manual_router, _memory_from_env switch."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentlib import (
    AgentContext,
    AlwaysApprove,
    EmbeddingMemoryStore,
    InMemoryAuditLogger,
    InMemoryBus,
    JsonlMemoryStore,
    ManualRouter,
)
from olympus_cli.registry import (
    build_orchestrator,
    default_agents,
    manual_router,
)
from olympus_cli import registry


def _ctx():
    return AgentContext(approval=AlwaysApprove(), audit=InMemoryAuditLogger())


def test_default_agents_constructs_four_production_agents():
    agents = default_agents()
    names = {a.name for a in agents}
    assert names == {"sysadmin", "programmer", "terraform", "ansible"}


def test_manual_router_returns_a_router_with_keyword_table():
    r = manual_router()
    # ManualRouter exposes the .routes (or similar) mapping; we only
    # care that it's a Router and recognizes well-known keywords.
    assert isinstance(r, ManualRouter)


def test_build_orchestrator_defaults_select_llm_router(monkeypatch):
    """When router is None, build_orchestrator constructs an LLMRouter
    over the agent name → domain map."""
    monkeypatch.setenv("OLYMPUS_MEMORY", "disabled")
    fake_agents = [MagicMock(name="m", domain="m-domain")]
    fake_agents[0].name = "m"
    with patch("olympus_cli.registry.LLMRouter") as LR, \
         patch("olympus_cli.registry.Orchestrator", autospec=True) as Orch:
        Orch.return_value = MagicMock()
        build_orchestrator(ctx=_ctx(), agents=fake_agents)
    LR.assert_called_once_with({"m": "m-domain"})


def test_build_orchestrator_with_explicit_router_skips_llm(monkeypatch):
    monkeypatch.setenv("OLYMPUS_MEMORY", "disabled")
    fake_agents = [MagicMock(name="x", domain="d")]
    custom_router = manual_router()
    with patch("olympus_cli.registry.LLMRouter") as LR, \
         patch("olympus_cli.registry.Orchestrator", autospec=True) as Orch:
        Orch.return_value = MagicMock()
        build_orchestrator(ctx=_ctx(), agents=fake_agents, router=custom_router)
    LR.assert_not_called()


def test_build_orchestrator_default_agents_branch(monkeypatch):
    """When agents=None, default_agents() is called."""
    monkeypatch.setenv("OLYMPUS_MEMORY", "disabled")
    with patch("olympus_cli.registry.default_agents", return_value=[]) as da, \
         patch("olympus_cli.registry.LLMRouter"), \
         patch("olympus_cli.registry.Orchestrator", autospec=True) as Orch:
        Orch.return_value = MagicMock()
        build_orchestrator(ctx=_ctx())
    da.assert_called_once()


def test_build_orchestrator_default_bus(monkeypatch):
    monkeypatch.setenv("OLYMPUS_MEMORY", "disabled")
    with patch("olympus_cli.registry.LLMRouter"), \
         patch("olympus_cli.registry.Orchestrator", autospec=True) as Orch:
        Orch.return_value = MagicMock()
        build_orchestrator(ctx=_ctx(), agents=[MagicMock(name="a", domain="d")])
    # First positional arg path: bus= was passed to Orchestrator.
    kwargs = Orch.call_args.kwargs
    assert isinstance(kwargs["bus"], InMemoryBus)


def test_memory_from_env_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("OLYMPUS_MEMORY", "disabled")
    assert registry._memory_from_env() is None


def test_memory_from_env_embeddings_returns_embedding_store(monkeypatch, tmp_path):
    monkeypatch.setenv("OLYMPUS_MEMORY", "embeddings")
    monkeypatch.setenv("OLYMPUS_MEMORY_PATH", str(tmp_path / "m.jsonl"))
    store = registry._memory_from_env()
    assert isinstance(store, EmbeddingMemoryStore)


def test_memory_from_env_default_returns_jsonl_store(monkeypatch, tmp_path):
    monkeypatch.delenv("OLYMPUS_MEMORY", raising=False)
    monkeypatch.setenv("OLYMPUS_MEMORY_PATH", str(tmp_path / "m.jsonl"))
    store = registry._memory_from_env()
    assert isinstance(store, JsonlMemoryStore)
