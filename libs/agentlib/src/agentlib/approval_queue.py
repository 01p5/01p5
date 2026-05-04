"""
QueueApprovalHook — in-process approval queue for the web dashboard.

Mirrors ``WebhookApprovalHook`` in shape but keeps everything in
memory: the dashboard backend owns this hook, exposes the pending
queue as JSON over HTTP, and resolves approvals via a sibling endpoint.
No external POST roundtrip.

Concurrency model: one in-flight request blocks on
``threading.Event``. Multiple agents can have approvals pending
simultaneously; the queue keys by ``approval_id``.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .spec import ApprovalDecision


@dataclass
class PendingApproval:
    approval_id: str
    agent: str
    tool: str
    args: dict[str, Any]
    rationale: str
    diff: Optional[str]
    requested_at: float
    decision: Optional[ApprovalDecision] = None
    event: threading.Event = field(default_factory=threading.Event)


class QueueApprovalHook:
    """ApprovalHook backed by an in-memory queue + per-request events.

    ``request()`` blocks until either ``resolve()`` is called for the
    same ``approval_id`` or ``approval_timeout_seconds`` elapses.
    """

    def __init__(self, approval_timeout_seconds: float = 600.0):
        self._approval_timeout = approval_timeout_seconds
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}

    # ---- ApprovalHook protocol ----

    def request(
        self,
        agent: str,
        tool: str,
        args: dict[str, Any],
        rationale: str,
        diff: Optional[str] = None,
    ) -> ApprovalDecision:
        approval = PendingApproval(
            approval_id=str(uuid.uuid4()),
            agent=agent,
            tool=tool,
            args=args,
            rationale=rationale,
            diff=diff,
            requested_at=time.time(),
        )
        with self._lock:
            self._pending[approval.approval_id] = approval

        if not approval.event.wait(timeout=self._approval_timeout):
            with self._lock:
                self._pending.pop(approval.approval_id, None)
            return ApprovalDecision(
                approved=False,
                reason=f"approval timed out after {self._approval_timeout:.0f}s",
            )

        with self._lock:
            self._pending.pop(approval.approval_id, None)
        return approval.decision or ApprovalDecision(
            approved=False, reason="resolved with no decision"
        )

    # ---- Dashboard-facing API ----

    def pending(self) -> list[PendingApproval]:
        with self._lock:
            return list(self._pending.values())

    def resolve(
        self,
        approval_id: str,
        approved: bool,
        reason: str,
        modified_args: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Set the decision for a pending approval. Returns False if
        the id is unknown (already resolved or never existed)."""
        with self._lock:
            approval = self._pending.get(approval_id)
        if approval is None:
            return False
        approval.decision = ApprovalDecision(
            approved=approved, reason=reason, modified_args=modified_args
        )
        approval.event.set()
        return True
