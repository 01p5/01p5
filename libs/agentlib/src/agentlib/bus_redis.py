"""
RedisStreamsBus — v2 backend for the Olympus context bus.

Per docs/BUS_DECISION.md, Redis Streams is the chosen v2 backend. This
module implements the same ``subscribe`` / ``publish`` / ``log`` shape
as ``InMemoryBus`` so the orchestrator and the dashboard backend can
swap one for the other with no other code changes.

Topology:
  - One stream per recipient: ``olympus:bus:{recipient}``. ``XADD``
    appends; consumers ``XREAD BLOCK`` from the last id they saw.
  - One mirror "all" stream: ``olympus:bus:_all``. Every publish writes
    to both the recipient stream and ``_all`` so ``"*"`` subscribers
    can tail one place.
  - Each ``subscribe(recipient, cb)`` spawns a daemon thread that
    blocks on ``XREAD`` against the recipient's stream and dispatches
    callbacks. ``"*"`` subscribers tail ``_all``.

Serialization:
  - The bus envelope is JSON. Dataclass payloads are converted with
    ``dataclasses.asdict``. Non-JSON-safe payloads fall back to
    ``repr()`` rather than raising — losing fidelity is preferable to
    losing a message in the audit trail. Consumers re-hydrate
    structured payloads (``TaskMessage``, ``AgentResult``) themselves;
    the bus stays payload-agnostic.

Why not async / aioredis: the rest of agentlib is synchronous in v1.
We'll move to async when the bus does, not before.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import threading
from typing import Any, Optional

from .bus import BusMessage, Subscriber

logger = logging.getLogger(__name__)

_KEY_PREFIX = "olympus:bus"
_ALL_STREAM = f"{_KEY_PREFIX}:_all"

# How long a consumer thread blocks per XREAD before checking the stop
# flag. Keep generous so idle consumers do not poll Redis hard, but
# small enough that ``close()`` returns within a few hundred ms.
_BLOCK_MS = 500


def _stream_for(recipient: str) -> str:
    if recipient == "*":
        return _ALL_STREAM
    return f"{_KEY_PREFIX}:{recipient}"


def _to_jsonable(value: Any) -> Any:
    """Best-effort JSON-safe conversion. Lossy on unknown types."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(dataclasses.asdict(value))
    if isinstance(value, set):
        return [_to_jsonable(v) for v in value]
    return repr(value)


def _encode(msg: BusMessage) -> dict[str, str]:
    """BusMessage → field map suitable for ``XADD``.

    Redis Streams field values must be strings; we keep one ``json``
    field with the full envelope so decoding is a single json.loads.
    """
    return {
        "json": json.dumps(
            {
                "msg_id": msg.msg_id,
                "task_id": msg.task_id,
                "sender": msg.sender,
                "recipient": msg.recipient,
                "kind": msg.kind,
                "timestamp": msg.timestamp,
                "payload": _to_jsonable(msg.payload),
                "causation_id": msg.causation_id,
            }
        )
    }


def _decode(fields: dict[bytes | str, bytes | str]) -> BusMessage:
    """``XREAD`` field-map → BusMessage. Tolerant of bytes-vs-str keys."""
    raw = fields.get("json") or fields.get(b"json")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    obj = json.loads(raw)
    return BusMessage(
        msg_id=obj["msg_id"],
        task_id=obj["task_id"],
        sender=obj["sender"],
        recipient=obj["recipient"],
        kind=obj["kind"],
        timestamp=obj["timestamp"],
        payload=obj.get("payload"),
        causation_id=obj.get("causation_id"),
    )


class RedisStreamsBus:
    """Redis Streams implementation of the bus contract.

    Construct with ``RedisStreamsBus(redis_client)`` or
    ``RedisStreamsBus.from_url("redis://…")``. The Redis client is
    expected to be the standard ``redis-py`` synchronous client (or a
    drop-in like fakeredis) that exposes ``xadd``, ``xread``.
    """

    def __init__(self, client: Any, max_len: Optional[int] = 10_000):
        """``max_len``: capped XADD MAXLEN for non-`*` streams. Pass
        None to keep history forever (the JSONL audit log is the
        durable record; Redis is the live tier)."""
        self._client = client
        self._max_len = max_len
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._log_lock = threading.RLock()
        self._log: list[BusMessage] = []
        # Tail the all-stream into _log so .log keeps the same audit
        # semantics as InMemoryBus (live-readable history). The _all
        # stream is also where "*" subscribers attach.
        self._log_thread = threading.Thread(
            target=self._tail_into_log, name="bus-redis-log", daemon=True
        )
        self._log_thread.start()
        self._threads.append(self._log_thread)

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> "RedisStreamsBus":
        try:
            import redis  # local import — keep redis as an optional dep
        except ImportError as exc:
            raise ImportError(
                "RedisStreamsBus.from_url() requires the optional 'redis' package. "
                "Install with `pip install redis`."
            ) from exc
        return cls(redis.Redis.from_url(url, decode_responses=False), **kwargs)

    # ----- public bus contract -----

    def subscribe(self, recipient: str, callback: Subscriber) -> None:
        stream = _stream_for(recipient)
        thread = threading.Thread(
            target=self._consume,
            args=(stream, callback, recipient),
            name=f"bus-redis-sub:{recipient}",
            daemon=True,
        )
        thread.start()
        self._threads.append(thread)

    def publish(self, msg: BusMessage) -> None:
        fields = _encode(msg)
        # XADD to the recipient stream first so per-recipient consumers
        # see it without an extra hop, then mirror to the all-stream.
        kwargs: dict[str, Any] = {}
        if self._max_len is not None:
            kwargs["maxlen"] = self._max_len
            kwargs["approximate"] = True
        self._client.xadd(_stream_for(msg.recipient), fields, **kwargs)
        if msg.recipient != "*":
            # Avoid double-write when the recipient *is* "*".
            self._client.xadd(_ALL_STREAM, fields, **kwargs)

    @property
    def log(self) -> list[BusMessage]:
        with self._log_lock:
            return list(self._log)

    def close(self) -> None:
        """Stop every consumer thread. Safe to call more than once."""
        self._stop.set()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=2.0)

    def __enter__(self) -> "RedisStreamsBus":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ----- internal -----

    def _consume(self, stream: str, callback: Subscriber, recipient: str) -> None:
        last_id = "$"  # only deliver messages produced after subscribe()
        while not self._stop.is_set():
            try:
                resp = self._client.xread({stream: last_id}, block=_BLOCK_MS, count=64)
            except Exception:
                logger.exception("xread failed for %s", stream)
                # Avoid a tight retry loop on persistent errors.
                if self._stop.wait(timeout=1.0):
                    return
                continue
            if not resp:
                continue
            for _stream_name, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id if isinstance(entry_id, str) else entry_id.decode()
                    try:
                        msg = _decode(fields)
                    except Exception:
                        logger.exception("could not decode message on %s", stream)
                        continue
                    # When recipient="*" subscribed to _all, replay everything.
                    # Otherwise drop messages whose envelope recipient does
                    # not match (defensive — XADD topology should already
                    # have prevented this).
                    if recipient != "*" and msg.recipient != recipient:
                        continue
                    try:
                        callback(msg)
                    except Exception:
                        logger.exception("subscriber for %s raised", recipient)

    def _tail_into_log(self) -> None:
        last_id = "0"  # capture full history including pre-subscribe writes
        while not self._stop.is_set():
            try:
                resp = self._client.xread({_ALL_STREAM: last_id}, block=_BLOCK_MS, count=128)
            except Exception:
                logger.exception("xread failed for log tail")
                if self._stop.wait(timeout=1.0):
                    return
                continue
            if not resp:
                continue
            for _stream_name, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id if isinstance(entry_id, str) else entry_id.decode()
                    try:
                        msg = _decode(fields)
                    except Exception:
                        continue
                    with self._log_lock:
                        self._log.append(msg)
