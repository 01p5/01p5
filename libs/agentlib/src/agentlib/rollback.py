"""
Rollback layer — per-destructive-verb inverse operations.

When a destructive tool fires successfully (e.g. ``write_file``,
``delete_pod``, ``tf_apply``), the runtime captures a ``RollbackPlan``
that describes how to undo it — which tool to call, with what args,
and the pre-state snapshot for audit. The plan is persisted in a
``RollbackStore`` keyed on a generated id and indexed by task_id.

Executing a rollback is itself a destructive operation: the
dashboard's ``POST /rollback/{id}/execute`` re-routes through
``gate_tools`` so the user re-approves the inverse before it fires.
That's intentional — the system never assumes the user wants the
undo, only that the *option* is available.

Two backends ship with v1, both implementing ``RollbackStore``:

- ``NullRollbackStore`` — drop-in no-op for tests + the default
  config when persistence isn't worth the cost.
- ``JsonlRollbackStore`` — append-only JSONL with a tmp-rename
  rewrite on ``mark_executed``. Same pattern as ``JsonlMemoryStore``.

The agent contract for opting in:

    class MyAgent(AgentSpec):
        ...
        rollback_snapshots = {
            "write_file": _snapshot_write_file,  # callable[[args], RollbackPlan]
        }

An agent that does not declare ``rollback_snapshots`` (or whose
destructive verbs aren't in the dict) silently opts out of rollback
for those verbs — no breakage, just no undo button.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol


@dataclass
class RollbackPlan:
    """The inverse-operation description an agent's snapshot fn returns.

    The runtime fills in the bookkeeping fields (task_id, agent,
    forward tool/args, ts, id, executed) to produce a
    ``RollbackEntry``.

    Fields:
      - ``inverse_tool``: the tool name that undoes the forward op.
      - ``inverse_args``: args to pass to that tool.
      - ``description``: human-readable, surfaces in the approval
        card when the rollback is executed.
      - ``snapshot``: free-form dict of pre-state for audit /
        inspection. Not used to execute the rollback (``inverse_args``
        already has everything the inverse tool needs); kept so a
        human can verify the rollback is correct before approving.
    """

    inverse_tool: str
    inverse_args: dict[str, Any]
    description: str
    snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class RollbackEntry:
    """A persisted rollback record. Identified by ``rollback_id``."""

    rollback_id: str
    task_id: str
    agent: str
    forward_tool: str
    forward_args: dict[str, Any]
    inverse_tool: str
    inverse_args: dict[str, Any]
    description: str
    snapshot: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    executed: bool = False
    executed_ts: Optional[float] = None
    executed_result: Optional[str] = None


def new_rollback_id() -> str:
    return uuid.uuid4().hex


class RollbackStore(Protocol):
    def write(self, entry: RollbackEntry) -> None: ...
    def get(self, rollback_id: str) -> Optional[RollbackEntry]: ...
    def list_for_task(self, task_id: str) -> list[RollbackEntry]: ...
    def list_recent(self, k: int = 25) -> list[RollbackEntry]: ...
    def mark_executed(
        self, rollback_id: str, result: Optional[str] = None
    ) -> bool: ...


class NullRollbackStore:
    """Drop-in no-op. The orchestrator falls back to this when no
    rollback store is configured."""

    def write(self, entry: RollbackEntry) -> None:
        return None

    def get(self, rollback_id: str) -> Optional[RollbackEntry]:
        return None

    def list_for_task(self, task_id: str) -> list[RollbackEntry]:
        return []

    def list_recent(self, k: int = 25) -> list[RollbackEntry]:
        return []

    def mark_executed(
        self, rollback_id: str, result: Optional[str] = None
    ) -> bool:
        return False


class InMemoryRollbackStore:
    """Process-local store. Useful for tests and short-lived
    deployments. Thread-safe."""

    def __init__(self) -> None:
        self._entries: list[RollbackEntry] = []
        self._lock = threading.Lock()

    def write(self, entry: RollbackEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def get(self, rollback_id: str) -> Optional[RollbackEntry]:
        with self._lock:
            for e in self._entries:
                if e.rollback_id == rollback_id:
                    return e
        return None

    def list_for_task(self, task_id: str) -> list[RollbackEntry]:
        with self._lock:
            return [e for e in self._entries if e.task_id == task_id]

    def list_recent(self, k: int = 25) -> list[RollbackEntry]:
        with self._lock:
            return list(reversed(self._entries))[:k]

    def mark_executed(
        self, rollback_id: str, result: Optional[str] = None
    ) -> bool:
        with self._lock:
            for e in self._entries:
                if e.rollback_id == rollback_id:
                    e.executed = True
                    e.executed_ts = time.time()
                    e.executed_result = result
                    return True
        return False


class JsonlRollbackStore:
    """Append-only JSONL on disk. ``mark_executed`` does a tmp-rename
    rewrite (same pattern as ``JsonlMemoryStore.annotate``).

    Each line is one ``RollbackEntry`` as JSON. The rewrite path is
    O(n) but n is bounded by the count of destructive successes in the
    deployment — well under 10k for a one-person setup."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, entry: RollbackEntry) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

    def _read_all(self) -> list[RollbackEntry]:
        if not self.path.exists():
            return []
        entries: list[RollbackEntry] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(RollbackEntry(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    # Skip malformed lines rather than fail the read.
                    continue
        return entries

    def get(self, rollback_id: str) -> Optional[RollbackEntry]:
        with self._lock:
            entries = self._read_all()
        for e in entries:
            if e.rollback_id == rollback_id:
                return e
        return None

    def list_for_task(self, task_id: str) -> list[RollbackEntry]:
        with self._lock:
            entries = self._read_all()
        return [e for e in entries if e.task_id == task_id]

    def list_recent(self, k: int = 25) -> list[RollbackEntry]:
        with self._lock:
            entries = self._read_all()
        return list(reversed(entries))[:k]

    def mark_executed(
        self, rollback_id: str, result: Optional[str] = None
    ) -> bool:
        with self._lock:
            entries = self._read_all()
            found = False
            for e in entries:
                if e.rollback_id == rollback_id:
                    e.executed = True
                    e.executed_ts = time.time()
                    e.executed_result = result
                    found = True
                    break
            if not found:
                return False
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(asdict(e)) + "\n")
            tmp.replace(self.path)
            return True


def plan_to_entry(
    plan: RollbackPlan,
    *,
    task_id: str,
    agent: str,
    forward_tool: str,
    forward_args: dict[str, Any],
) -> RollbackEntry:
    """Convert a ``RollbackPlan`` (what the agent declared) into a
    ``RollbackEntry`` (what gets persisted). Pure function — the
    runtime calls this right before ``store.write``."""
    return RollbackEntry(
        rollback_id=new_rollback_id(),
        task_id=task_id,
        agent=agent,
        forward_tool=forward_tool,
        forward_args=dict(forward_args),
        inverse_tool=plan.inverse_tool,
        inverse_args=dict(plan.inverse_args),
        description=plan.description,
        snapshot=dict(plan.snapshot),
    )
