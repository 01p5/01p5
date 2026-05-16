"""
Tests for the agentlib rollback layer.

Covers:
  - RollbackPlan + RollbackEntry dataclass shape + plan_to_entry helper.
  - NullRollbackStore no-op contract.
  - InMemoryRollbackStore + JsonlRollbackStore: write, get,
    list_for_task, list_recent, mark_executed semantics, malformed-line
    tolerance, persistence across instances (Jsonl only).
  - new_rollback_id produces collision-resistant ids.

Runtime/agent integration is covered in test_runtime.py +
test_programmer_smoke.py.
"""
from __future__ import annotations

import json

from agentlib import (
    InMemoryRollbackStore,
    JsonlRollbackStore,
    NullRollbackStore,
    RollbackEntry,
    RollbackPlan,
    new_rollback_id,
    plan_to_entry,
)


def _plan(
    inverse_tool: str = "write_file",
    inverse_args: dict | None = None,
    description: str = "restore /tmp/foo",
    snapshot: dict | None = None,
) -> RollbackPlan:
    return RollbackPlan(
        inverse_tool=inverse_tool,
        inverse_args=inverse_args or {"path": "/tmp/foo", "content": "before"},
        description=description,
        snapshot=snapshot or {"prior_exists": True, "prior_content": "before"},
    )


# ---------------------------------------------------------------------
# IDs + plan_to_entry
# ---------------------------------------------------------------------


def test_new_rollback_id_unique_per_call():
    seen = {new_rollback_id() for _ in range(100)}
    assert len(seen) == 100  # no collisions


def test_plan_to_entry_fills_bookkeeping_and_copies_args():
    plan = _plan()
    entry = plan_to_entry(
        plan,
        task_id="T1",
        agent="programmer",
        forward_tool="write_file",
        forward_args={"path": "/tmp/foo", "content": "after"},
    )
    assert entry.rollback_id  # not empty
    assert entry.task_id == "T1"
    assert entry.agent == "programmer"
    assert entry.forward_tool == "write_file"
    assert entry.forward_args == {"path": "/tmp/foo", "content": "after"}
    assert entry.inverse_tool == plan.inverse_tool
    assert entry.inverse_args == plan.inverse_args
    assert entry.description == plan.description
    assert entry.snapshot == plan.snapshot
    assert entry.executed is False
    assert entry.executed_ts is None
    # Mutating the args dict on the plan must not bleed into the entry.
    plan.inverse_args["mutated"] = True
    assert "mutated" not in entry.inverse_args


# ---------------------------------------------------------------------
# NullRollbackStore
# ---------------------------------------------------------------------


def test_null_store_is_noop():
    s = NullRollbackStore()
    s.write(plan_to_entry(_plan(), task_id="T", agent="a",
                          forward_tool="x", forward_args={}))
    assert s.get("anything") is None
    assert s.list_for_task("T") == []
    assert s.list_recent() == []
    assert s.mark_executed("anything") is False


# ---------------------------------------------------------------------
# InMemoryRollbackStore
# ---------------------------------------------------------------------


def _store_and_entries(store):
    e1 = plan_to_entry(
        _plan(description="restore foo"),
        task_id="T1", agent="programmer",
        forward_tool="write_file",
        forward_args={"path": "/tmp/foo", "content": "after"},
    )
    e2 = plan_to_entry(
        _plan(description="restore bar"),
        task_id="T1", agent="programmer",
        forward_tool="write_file",
        forward_args={"path": "/tmp/bar", "content": "after"},
    )
    e3 = plan_to_entry(
        _plan(description="recreate pod"),
        task_id="T2", agent="sysadmin",
        forward_tool="delete_pod",
        forward_args={"name": "web", "namespace": "default"},
    )
    store.write(e1)
    store.write(e2)
    store.write(e3)
    return e1, e2, e3


def test_in_memory_store_write_and_get():
    s = InMemoryRollbackStore()
    e1, e2, e3 = _store_and_entries(s)
    assert s.get(e1.rollback_id) == e1
    assert s.get(e2.rollback_id) == e2
    assert s.get(e3.rollback_id) == e3
    assert s.get("ghost") is None


def test_in_memory_store_list_for_task_filters_correctly():
    s = InMemoryRollbackStore()
    e1, e2, e3 = _store_and_entries(s)
    t1_entries = s.list_for_task("T1")
    assert {e.rollback_id for e in t1_entries} == {e1.rollback_id, e2.rollback_id}
    t2_entries = s.list_for_task("T2")
    assert [e.rollback_id for e in t2_entries] == [e3.rollback_id]
    assert s.list_for_task("ghost") == []


def test_in_memory_store_list_recent_returns_newest_first():
    s = InMemoryRollbackStore()
    e1, e2, e3 = _store_and_entries(s)
    recent = s.list_recent(k=5)
    assert [e.rollback_id for e in recent] == [e3.rollback_id, e2.rollback_id, e1.rollback_id]
    # Honour k.
    assert len(s.list_recent(k=1)) == 1


def test_in_memory_store_mark_executed_sets_fields():
    s = InMemoryRollbackStore()
    e1, _, _ = _store_and_entries(s)
    assert s.mark_executed(e1.rollback_id, result="ok") is True
    refreshed = s.get(e1.rollback_id)
    assert refreshed.executed is True
    assert refreshed.executed_ts is not None
    assert refreshed.executed_result == "ok"


def test_in_memory_store_mark_executed_unknown_id_returns_false():
    s = InMemoryRollbackStore()
    assert s.mark_executed("ghost") is False


# ---------------------------------------------------------------------
# JsonlRollbackStore
# ---------------------------------------------------------------------


def test_jsonl_store_persists_across_instances(tmp_path):
    path = tmp_path / "rollback.jsonl"
    s1 = JsonlRollbackStore(path)
    e1, _, _ = _store_and_entries(s1)

    s2 = JsonlRollbackStore(path)
    assert s2.get(e1.rollback_id) == e1
    assert {e.rollback_id for e in s2.list_for_task("T1")} >= {e1.rollback_id}


def test_jsonl_store_creates_parent_dir(tmp_path):
    nested = tmp_path / "a" / "b" / "rollback.jsonl"
    JsonlRollbackStore(nested)
    assert nested.parent.is_dir()


def test_jsonl_store_tolerates_malformed_lines(tmp_path):
    path = tmp_path / "rollback.jsonl"
    s = JsonlRollbackStore(path)
    e1, _, _ = _store_and_entries(s)
    with path.open("a", encoding="utf-8") as f:
        f.write("not valid json\n")
        f.write('{"only": "partial"}\n')
        f.write("\n")
    # Reading still returns the valid entries.
    assert s.get(e1.rollback_id) == e1


def test_jsonl_store_mark_executed_atomic_rewrite(tmp_path):
    path = tmp_path / "rollback.jsonl"
    s = JsonlRollbackStore(path)
    e1, e2, _ = _store_and_entries(s)
    assert s.mark_executed(e2.rollback_id, result="rolled back") is True

    # File still parses cleanly + has all entries.
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 3
    for line in lines:
        rec = json.loads(line)
        RollbackEntry(**rec)  # roundtrips

    # The targeted entry shows executed=True; the others stay False.
    refreshed = s.get(e2.rollback_id)
    assert refreshed.executed is True
    assert refreshed.executed_result == "rolled back"
    assert s.get(e1.rollback_id).executed is False


def test_jsonl_store_mark_executed_unknown_id_does_not_rewrite(tmp_path):
    path = tmp_path / "rollback.jsonl"
    s = JsonlRollbackStore(path)
    _store_and_entries(s)
    before = path.read_text()
    assert s.mark_executed("ghost") is False
    after = path.read_text()
    assert before == after  # untouched
