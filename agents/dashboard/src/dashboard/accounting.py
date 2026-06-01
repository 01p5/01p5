"""Durable per-task cost ledger — SQLite-backed.

Replaces the in-memory-only accounting that reset on every pod restart
(taking each user's daily spend with it, so caps didn't hold across
reboots). One row per task; the admin accounting + activity views and
the daily-limit enforcement all read from here.

SQLite because the dashboard is a single replica — no multi-writer
concern, no extra service, just a file on the persistent volume. A
single shared connection (check_same_thread=False) serialized by a
lock; WAL mode so a crash mid-write can't corrupt the db. Pass
``:memory:`` for an ephemeral store (tests / dev); build_default_server
points it at accounting.db on the audit PVC.

The live task list (GET /tasks) + SSE still use the in-memory
TaskRecord working set — that's ephemeral by design ("tasks this
session"). This store is the durable ledger written alongside it.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_costs (
    task_id          TEXT PRIMARY KEY,
    owner_email      TEXT,
    agent            TEXT,
    status           TEXT NOT NULL,
    cost_usd         REAL,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    wall_seconds     REAL,
    submitted_at     REAL NOT NULL,
    natural_language TEXT
);
CREATE INDEX IF NOT EXISTS idx_owner_submitted ON task_costs(owner_email, submitted_at);
CREATE INDEX IF NOT EXISTS idx_submitted ON task_costs(submitted_at);
"""


def _norm(email: Optional[str]) -> str:
    return (email or "").strip().lower()


class SqliteAccountingStore:
    """Per-task cost rows + the aggregations the admin dashboard needs."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the HTTP server + task worker threads
        # all touch this one connection; the lock serializes them.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ----- writes -----

    def record_submitted(
        self, task_id: str, *, owner_email: Optional[str], natural_language: str,
        submitted_at: float, agent: Optional[str] = None, status: str = "pending",
    ) -> None:
        """Insert a task at submit time (cost unknown yet). Idempotent —
        a re-submit of the same id replaces the row."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO task_costs
                   (task_id, owner_email, agent, status, submitted_at, natural_language)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET
                     owner_email=excluded.owner_email,
                     agent=excluded.agent,
                     status=excluded.status,
                     natural_language=excluded.natural_language""",
                (task_id, _norm(owner_email), agent, status, submitted_at,
                 (natural_language or "")[:500]),
            )
            self._conn.commit()

    def record_result(
        self, task_id: str, *, status: str, cost_usd: Optional[float],
        input_tokens: Optional[int], output_tokens: Optional[int],
        wall_seconds: Optional[float], agent: Optional[str] = None,
    ) -> None:
        """Update a task with its settled cost. No-op if the task_id was
        never recorded (shouldn't happen — record_submitted runs first)."""
        with self._lock:
            self._conn.execute(
                """UPDATE task_costs SET
                     status=?, cost_usd=?, input_tokens=?, output_tokens=?,
                     wall_seconds=?, agent=COALESCE(?, agent)
                   WHERE task_id=?""",
                (status, cost_usd, input_tokens, output_tokens, wall_seconds,
                 agent, task_id),
            )
            self._conn.commit()

    # ----- reads -----

    def spend_today_by_user(self, day_start: float) -> dict[str, float]:
        """{owner_email: sum(cost_usd)} for tasks submitted since day_start.
        Excludes the no-owner ('') bucket."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT owner_email, COALESCE(SUM(cost_usd), 0.0) AS spent
                   FROM task_costs
                   WHERE submitted_at >= ? AND owner_email <> ''
                   GROUP BY owner_email""",
                (day_start,),
            ).fetchall()
        return {r["owner_email"]: float(r["spent"]) for r in rows}

    def spend_today(self, email: str, day_start: float) -> float:
        with self._lock:
            row = self._conn.execute(
                """SELECT COALESCE(SUM(cost_usd), 0.0) AS spent
                   FROM task_costs
                   WHERE owner_email = ? AND submitted_at >= ?""",
                (_norm(email), day_start),
            ).fetchone()
        return float(row["spent"]) if row else 0.0

    def per_user(self, day_start: float) -> list[dict]:
        """Per-user lifetime rollup + today's spend. Settled-only sums for
        usd/tokens (matches the live telemetry semantics); 'tasks' counts
        everything, 'settled' counts the finished ones."""
        settled = "('success','failed','rejected','cancelled')"
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT
                      owner_email,
                      COUNT(*) AS tasks,
                      SUM(CASE WHEN status IN {settled} THEN 1 ELSE 0 END) AS settled,
                      COALESCE(SUM(CASE WHEN status IN {settled} THEN cost_usd END), 0.0) AS usd,
                      COALESCE(SUM(CASE WHEN status IN {settled} THEN input_tokens END), 0) AS input_tokens,
                      COALESCE(SUM(CASE WHEN status IN {settled} THEN output_tokens END), 0) AS output_tokens,
                      COALESCE(SUM(CASE WHEN status IN {settled} THEN wall_seconds END), 0.0) AS wall_seconds,
                      COALESCE(SUM(CASE WHEN submitted_at >= ? THEN cost_usd END), 0.0) AS spent_today
                    FROM task_costs
                    WHERE owner_email <> ''
                    GROUP BY owner_email""",
                (day_start,),
            ).fetchall()
        return [
            {
                "email": r["owner_email"],
                "tasks": int(r["tasks"]),
                "settled": int(r["settled"]),
                "usd": round(float(r["usd"]), 6),
                "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "wall_seconds": float(r["wall_seconds"]),
                "spent_today_usd": round(float(r["spent_today"]), 6),
            }
            for r in rows
        ]

    def recent_activity(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT task_id, owner_email, agent, status, submitted_at,
                          cost_usd, natural_language
                   FROM task_costs
                   ORDER BY submitted_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "task_id": r["task_id"],
                "owner_email": r["owner_email"] or None,
                "agent": r["agent"],
                "status": r["status"],
                "submitted_at": r["submitted_at"],
                "cost_usd": r["cost_usd"],
                "natural_language": (r["natural_language"] or "")[:160],
            }
            for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
