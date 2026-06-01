"""Per-user daily cost limits — file-backed, atomic, thread-safe.

ADM.3. The super-admin accounting dashboard lets an operator set a
per-user daily USD cap. This store persists those caps; enforcement
(comparing a user's spend-today against their cap) lives in the
dashboard server, which already holds the per-task cost records.

Mirrors the atomic read-modify-write pattern of
``agentlib.inventory.FileBackedInventoryStore``: one JSON file, written
via a temp-file rename so a crash mid-write can't corrupt it. An
in-memory variant backs the tests + any deploy without a writable
volume.

File shape::

    {
      "limits": {
        "alice@x.com": {"daily_limit_usd": 10.0, "updated_at": 1719792000.0},
        ...
      }
    }
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional, Protocol


class UserLimitStore(Protocol):
    """Per-user daily USD cap, keyed by lowercased email."""

    def get(self, email: str) -> Optional[float]:
        """Return the user's daily cap, or None if unset (no limit)."""
        ...

    def set(self, email: str, daily_limit_usd: Optional[float]) -> None:
        """Set (or clear, when None) the user's daily cap."""
        ...

    def all(self) -> dict[str, float]:
        """All configured caps, ``{email: usd}``."""
        ...


def _norm(email: str) -> str:
    return (email or "").strip().lower()


class InMemoryUserLimitStore:
    """Default store — fine for tests + ephemeral deploys (limits reset
    on restart). Production swaps in the file-backed variant."""

    def __init__(self) -> None:
        self._limits: dict[str, float] = {}
        self._lock = threading.RLock()

    def get(self, email: str) -> Optional[float]:
        with self._lock:
            return self._limits.get(_norm(email))

    def set(self, email: str, daily_limit_usd: Optional[float]) -> None:
        e = _norm(email)
        if not e:
            raise ValueError("email is required")
        with self._lock:
            if daily_limit_usd is None:
                self._limits.pop(e, None)
            else:
                if daily_limit_usd < 0:
                    raise ValueError("daily_limit_usd must be >= 0")
                self._limits[e] = float(daily_limit_usd)

    def all(self) -> dict[str, float]:
        with self._lock:
            return dict(self._limits)


class FileBackedUserLimitStore:
    """JSON-on-disk, atomic write, RLock-guarded. Same shape contract as
    InMemoryUserLimitStore."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, float]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text("utf-8") or "{}")
        except (json.JSONDecodeError, OSError):
            return {}
        out: dict[str, float] = {}
        for email, rec in (raw.get("limits") or {}).items():
            try:
                out[_norm(email)] = float(rec["daily_limit_usd"])
            except (TypeError, KeyError, ValueError):
                continue
        return out

    def _write(self, limits: dict[str, float]) -> None:
        payload = {
            "limits": {
                e: {"daily_limit_usd": v, "updated_at": time.time()}
                for e, v in sorted(limits.items())
            }
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
        tmp.replace(self.path)

    def get(self, email: str) -> Optional[float]:
        with self._lock:
            return self._read().get(_norm(email))

    def set(self, email: str, daily_limit_usd: Optional[float]) -> None:
        e = _norm(email)
        if not e:
            raise ValueError("email is required")
        if daily_limit_usd is not None and daily_limit_usd < 0:
            raise ValueError("daily_limit_usd must be >= 0")
        with self._lock:
            limits = self._read()
            if daily_limit_usd is None:
                limits.pop(e, None)
            else:
                limits[e] = float(daily_limit_usd)
            self._write(limits)

    def all(self) -> dict[str, float]:
        with self._lock:
            return self._read()
