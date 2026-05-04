"""
In-memory context bus (v1).

Synchronous publish: ``publish(msg)`` appends to the replay log, then
calls every matching subscriber inline before returning. This is fine
for the W1-2 PoC and the W3-4 single-process orchestrator. The
v2 bus (Redis streams or NATS) replaces this module without changing
the public API.

The append-only ``log`` is the audit trail — every cross-agent message
is preserved in order, regardless of subscriber state.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional, Protocol

BusKind = Literal[
    "task",
    "result",
    "progress",
    "log",
    "approval_request",
    "approval_decision",
]


@dataclass
class BusMessage:
    msg_id: str
    task_id: str
    sender: str
    recipient: str  # agent name, "orchestrator", or "*" for broadcast
    kind: BusKind
    timestamp: float
    payload: Any  # spec says dict; v1 accepts any Python object — v2 must be JSON-safe
    causation_id: Optional[str] = None


def new_message(
    task_id: str,
    sender: str,
    recipient: str,
    kind: BusKind,
    payload: Any,
    causation_id: Optional[str] = None,
) -> BusMessage:
    return BusMessage(
        msg_id=str(uuid.uuid4()),
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        kind=kind,
        timestamp=time.time(),
        payload=payload,
        causation_id=causation_id,
    )


Subscriber = Callable[[BusMessage], None]


class Bus(Protocol):
    """Minimal contract every bus backend (in-memory, Redis, NATS, …)
    must satisfy. The orchestrator and dashboard backend program
    against this — not against any specific implementation."""

    def subscribe(self, recipient: str, callback: Subscriber) -> None: ...
    def publish(self, msg: BusMessage) -> None: ...
    @property
    def log(self) -> list[BusMessage]: ...


class InMemoryBus:
    """Synchronous in-process bus.

    Routing rule: a subscriber registered for recipient ``X`` receives
    messages addressed to ``X`` or to ``"*"``. A subscriber registered
    for ``"*"`` receives every message (used by audit/log sinks).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = {}
        self._log: list[BusMessage] = []
        self._lock = threading.RLock()

    def subscribe(self, recipient: str, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers.setdefault(recipient, []).append(callback)

    def publish(self, msg: BusMessage) -> None:
        with self._lock:
            self._log.append(msg)
            targets = list(self._subscribers.get(msg.recipient, []))
            if msg.recipient != "*":
                targets += list(self._subscribers.get("*", []))
        # Call subscribers outside the lock to allow re-entrant publish.
        for cb in targets:
            cb(msg)

    @property
    def log(self) -> list[BusMessage]:
        """Append-only message history (full audit trail)."""
        with self._lock:
            return list(self._log)
