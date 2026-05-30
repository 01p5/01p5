"""
Tests for AnsibleAgent's per-handle inventory materialization.

We don't run ansible — we verify that handle() materializes a run dir
from the InventoryStore, threads its path into the system prompt, and
cleans up the dir on both success and exception paths.
"""
from __future__ import annotations

import os
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from agentlib import (
    AgentContext,
    AlwaysApprove,
    InMemoryAuditLogger,
    InMemoryInventoryStore,
    TaskMessage,
)
from ansible_agent.agent import AnsibleAgent, AnsibleResponse


FAKE_KEY = textwrap.dedent(
    """
    -----BEGIN OPENSSH PRIVATE KEY-----
    AAAAB3NzaC1yc2EAAAADAQABAAABAQDansible
    -----END OPENSSH PRIVATE KEY-----
    """
).strip()


@pytest.fixture
def populated_store():
    s = InMemoryInventoryStore()
    k = s.add_key(name="prod", content=FAKE_KEY)
    s.add_host(name="cp", address="10.0.0.1", key_id=k.id,
               groups=["control_plane"])
    s.add_host(name="w1", address="10.0.0.2", key_id=k.id,
               groups=["workers"])
    return s


def _ctx(store=None):
    return AgentContext(
        approval=AlwaysApprove(),
        audit=InMemoryAuditLogger(),
        inventory_store=store,
    )


def test_system_prompt_mentions_managed_inventory_path(populated_store):
    spec = AnsibleAgent()
    captured: dict[str, str] = {}

    def fake_structural(*, system_prompt, **kwargs):
        captured["prompt"] = system_prompt
        m = MagicMock()
        m.invoke.return_value = AnsibleResponse(summary="ok")
        return m

    with patch("ansible_agent.agent.StructuralAgent", side_effect=fake_structural), \
         patch("ansible_agent.agent.cost_from_agent", return_value=None):
        spec.handle(TaskMessage(task_id="t", natural_language="check"), _ctx(populated_store))

    prompt = captured["prompt"]
    assert "Managed inventory" in prompt
    assert "Holds 2 host" in prompt
    # The exact path is dynamic (mkdtemp) but it must reference inventory.ini.
    assert "inventory.ini" in prompt


def test_system_prompt_with_empty_store_says_no_managed():
    spec = AnsibleAgent()
    captured: dict[str, str] = {}

    def fake_structural(*, system_prompt, **kwargs):
        captured["prompt"] = system_prompt
        m = MagicMock()
        m.invoke.return_value = AnsibleResponse(summary="ok")
        return m

    with patch("ansible_agent.agent.StructuralAgent", side_effect=fake_structural), \
         patch("ansible_agent.agent.cost_from_agent", return_value=None):
        spec.handle(TaskMessage(task_id="t", natural_language="check"),
                    _ctx(InMemoryInventoryStore()))

    prompt = captured["prompt"]
    assert "No managed inventory" in prompt


def test_system_prompt_with_no_store_says_no_managed():
    spec = AnsibleAgent()
    captured: dict[str, str] = {}

    def fake_structural(*, system_prompt, **kwargs):
        captured["prompt"] = system_prompt
        m = MagicMock()
        m.invoke.return_value = AnsibleResponse(summary="ok")
        return m

    with patch("ansible_agent.agent.StructuralAgent", side_effect=fake_structural), \
         patch("ansible_agent.agent.cost_from_agent", return_value=None):
        spec.handle(TaskMessage(task_id="t", natural_language="check"), _ctx(None))

    assert "No managed inventory" in captured["prompt"]


def test_run_dir_cleaned_up_on_success(populated_store):
    spec = AnsibleAgent()
    captured: dict[str, str] = {}

    def fake_structural(*, system_prompt, **kwargs):
        # Extract the run dir path from the prompt (path is the parent of
        # inventory.ini).
        for line in system_prompt.splitlines():
            if "Managed inventory" in line and "inventory.ini" in line:
                captured["path"] = line.split(":", 1)[1].strip().split()[0]
        m = MagicMock()
        m.invoke.return_value = AnsibleResponse(summary="ok")
        return m

    with patch("ansible_agent.agent.StructuralAgent", side_effect=fake_structural), \
         patch("ansible_agent.agent.cost_from_agent", return_value=None):
        spec.handle(TaskMessage(task_id="t", natural_language="check"), _ctx(populated_store))

    # The inventory file existed during handle, gone after.
    inv_path = captured["path"]
    run_dir = os.path.dirname(inv_path)
    assert not os.path.exists(run_dir), f"run dir {run_dir} should be cleaned up"


def test_run_dir_cleaned_up_on_exception(populated_store):
    spec = AnsibleAgent()
    captured: dict[str, str] = {}

    def fake_structural(*, system_prompt, **kwargs):
        for line in system_prompt.splitlines():
            if "Managed inventory" in line and "inventory.ini" in line:
                captured["path"] = line.split(":", 1)[1].strip().split()[0]
        m = MagicMock()
        m.invoke.side_effect = RuntimeError("ansible boom")
        return m

    with patch("ansible_agent.agent.StructuralAgent", side_effect=fake_structural), \
         patch("ansible_agent.agent.cost_from_agent", return_value=None):
        result = spec.handle(TaskMessage(task_id="t", natural_language="x"), _ctx(populated_store))

    assert result.status == "failed"
    run_dir = os.path.dirname(captured["path"])
    assert not os.path.exists(run_dir)
