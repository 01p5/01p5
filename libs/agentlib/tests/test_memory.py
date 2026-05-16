"""
Tests for the agentlib memory layer.

Covers:
  - MemoryEntry serialization round-trip + index/prompt rendering.
  - NullMemoryStore no-op contract.
  - InMemoryMemoryStore + JsonlMemoryStore: write/search ranking,
    agent filter, persistence (Jsonl only), malformed-line tolerance.
  - EmbeddingMemoryStore: fallback path when no API key is set
    (must not raise — must downgrade to RapidFuzz ranking).
  - render_memory_block formatting (empty list → empty string;
    populated list → quoted, treat-as-untrusted prefix).

The EmbeddingMemoryStore's happy path (real OpenAI calls) is not
covered here — it's an opt-in live test gated on OPENAI_API_KEY.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from agentlib import (
    EmbeddingMemoryStore,
    InMemoryMemoryStore,
    JsonlMemoryStore,
    MemoryEntry,
    NullMemoryStore,
    render_memory_block,
)


def _entry(
    task_id: str = "T1",
    agent: str = "sysadmin",
    nl: str = "list pods in default",
    summary: str = "found 3 running pods",
    status: str = "success",
) -> MemoryEntry:
    return MemoryEntry(
        task_id=task_id,
        agent=agent,
        natural_language=nl,
        summary=summary,
        status=status,
    )


# ---------------------------------------------------------------------
# MemoryEntry
# ---------------------------------------------------------------------


def test_memory_entry_round_trips_through_json():
    e = _entry()
    encoded = json.dumps(asdict(e))
    decoded = MemoryEntry(**json.loads(encoded))
    assert decoded == e


def test_memory_entry_index_text_combines_nl_and_summary():
    e = _entry(nl="delete pod X", summary="approved + deleted")
    text = e.index_text()
    assert "delete pod X" in text
    assert "approved + deleted" in text


def test_memory_entry_prompt_block_includes_agent_and_status():
    e = _entry(agent="terraform", status="failed", summary="state lock")
    block = e.to_prompt_block()
    assert "agent=terraform" in block
    assert "status=failed" in block
    assert "state lock" in block


# ---------------------------------------------------------------------
# NullMemoryStore
# ---------------------------------------------------------------------


def test_null_store_write_is_noop_and_search_returns_empty():
    s = NullMemoryStore()
    s.write(_entry())  # must not raise
    assert s.search("anything", k=5) == []
    assert s.search("anything", k=5, agent="sysadmin") == []


# ---------------------------------------------------------------------
# InMemoryMemoryStore
# ---------------------------------------------------------------------


def test_in_memory_store_returns_most_similar_first():
    s = InMemoryMemoryStore()
    s.write(_entry(task_id="T1", nl="delete pod web in default"))
    s.write(_entry(task_id="T2", nl="run terraform plan in pve"))
    s.write(_entry(task_id="T3", nl="delete pod nginx in default"))

    hits = s.search("delete pod foo in default", k=2)
    assert [h.task_id for h in hits] == ["T1", "T3"] or [
        h.task_id for h in hits
    ] == ["T3", "T1"]
    # T2 (terraform) must rank below the two pod-delete entries.
    assert "T2" not in {h.task_id for h in hits}


def test_in_memory_store_agent_filter():
    s = InMemoryMemoryStore()
    s.write(_entry(task_id="T1", agent="sysadmin", nl="delete pod web"))
    s.write(_entry(task_id="T2", agent="terraform", nl="delete the stack"))

    hits = s.search("delete", k=5, agent="sysadmin")
    assert [h.task_id for h in hits] == ["T1"]


def test_in_memory_store_empty_returns_empty():
    s = InMemoryMemoryStore()
    assert s.search("anything", k=3) == []


def test_in_memory_store_drops_zero_score_hits():
    """A query that shares no tokens with stored entries must still
    return an empty list rather than padding with junk."""
    s = InMemoryMemoryStore()
    s.write(_entry(nl="the quick brown fox jumps"))
    hits = s.search("zzzzzzz qqqqqqq", k=3)
    assert hits == []


# ---------------------------------------------------------------------
# JsonlMemoryStore
# ---------------------------------------------------------------------


def test_jsonl_store_persists_across_instances(tmp_path):
    path = tmp_path / "memory.jsonl"
    s1 = JsonlMemoryStore(path)
    s1.write(_entry(task_id="A", nl="delete pod foo"))
    s1.write(_entry(task_id="B", nl="run terraform plan"))

    s2 = JsonlMemoryStore(path)
    hits = s2.search("delete pod", k=2)
    assert any(h.task_id == "A" for h in hits)


def test_jsonl_store_creates_parent_dir(tmp_path):
    nested = tmp_path / "a" / "b" / "memory.jsonl"
    JsonlMemoryStore(nested)
    assert nested.parent.is_dir()


def test_jsonl_store_tolerates_malformed_lines(tmp_path):
    path = tmp_path / "memory.jsonl"
    s = JsonlMemoryStore(path)
    s.write(_entry(task_id="ok", nl="delete pod nginx"))
    with path.open("a", encoding="utf-8") as f:
        f.write("not valid json\n")
        f.write('{"only": "partial"}\n')  # missing required fields
        f.write("\n")  # blank line
    # Read still returns the valid entry without raising.
    hits = s.search("delete pod nginx", k=5)
    assert any(h.task_id == "ok" for h in hits)


def test_jsonl_store_agent_filter(tmp_path):
    s = JsonlMemoryStore(tmp_path / "memory.jsonl")
    s.write(_entry(task_id="X", agent="sysadmin", nl="delete pod"))
    s.write(_entry(task_id="Y", agent="programmer", nl="write a dockerfile"))
    # Query shares tokens with both stored entries, but agent filter
    # must collapse the result down to the programmer's entry.
    hits = s.search("write a pod dockerfile delete", k=5, agent="programmer")
    assert [h.task_id for h in hits] == ["Y"]


# ---------------------------------------------------------------------
# EmbeddingMemoryStore — fallback path only (no API key required)
# ---------------------------------------------------------------------


def test_embedding_store_without_api_key_falls_back_to_lexical(
    tmp_path, monkeypatch
):
    """No OPENAI_API_KEY → _embed raises → fallback runs → still
    returns reasonable hits. The store must never crash here."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = EmbeddingMemoryStore(tmp_path / "emb.jsonl")
    # Write should not raise even though embedding fails.
    s.write(_entry(task_id="A", nl="delete pod web"))
    s.write(_entry(task_id="B", nl="run terraform plan"))

    hits = s.search("delete pod nginx", k=2)
    assert any(h.task_id == "A" for h in hits)


def test_embedding_store_persists_failed_embedding_rows(tmp_path, monkeypatch):
    """Even when embedding fails, the entry must be persisted so a
    future call (when the API is reachable) doesn't lose history."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "emb.jsonl"
    s1 = EmbeddingMemoryStore(path)
    s1.write(_entry(task_id="A", nl="delete pod foo"))

    # File exists and has one record.
    assert path.exists()
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["entry"]["task_id"] == "A"
    # Vec is an empty list when embedding failed.
    assert record["vec"] == []


# ---------------------------------------------------------------------
# render_memory_block
# ---------------------------------------------------------------------


def test_render_memory_block_empty_returns_empty_string():
    assert render_memory_block([]) == ""


def test_render_memory_block_warns_treat_as_untrusted():
    block = render_memory_block([_entry()])
    # The prefix must explicitly mark the block as untrusted so an
    # injection in a prior summary can't escalate.
    assert "untrusted" in block.lower()
    assert "---" in block  # the separator before the real task


def test_render_memory_block_includes_each_entry():
    entries = [
        _entry(task_id="T1", nl="task one", summary="outcome one"),
        _entry(task_id="T2", nl="task two", summary="outcome two"),
    ]
    block = render_memory_block(entries)
    assert "task one" in block
    assert "outcome one" in block
    assert "task two" in block
    assert "outcome two" in block
