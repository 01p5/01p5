"""
Budget-aware gating for LLM calls.

Prevents agents from DoS-ing the LLM service when a spending limit is
exceeded.  Supports two complementary detection modes:

1. **Proactive** (cost_getter): check local accumulated cost before calling.
2. **Reactive** (server error): catch ``budget_exceeded`` HTTP 400 errors
   returned by the LLM gateway (e.g. LiteLLM) and enter backoff.

Provides:

* Thread-safe state machine: OK → EXCEEDED → WAITING → RESUMED → OK
* Exponential back-off with jitter (no tight retry loops)
* Hard minimum interval between probes
* Circuit-breaker: raises ``BudgetExceededError`` after max-wait window
* Telemetry helpers for state-transition logging & suppressed-request counts
"""

from __future__ import annotations

import enum
import logging
import random
import re
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Public exceptions
# ------------------------------------------------------------------

class BudgetExceededError(Exception):
    """Raised when budget is exhausted and the circuit-breaker fires."""


# ------------------------------------------------------------------
# State enum
# ------------------------------------------------------------------

class BudgetState(str, enum.Enum):
    OK = "OK"
    EXCEEDED = "EXCEEDED"
    WAITING = "WAITING"
    RESUMED = "RESUMED"


# ------------------------------------------------------------------
# BudgetGuard
# ------------------------------------------------------------------

class BudgetGuard:
    """Thread-safe budget gate for LLM requests.

    Parameters
    ----------
    max_budget : float
        Maximum allowed spend (same currency unit as *cost_getter*).
    cost_getter : callable, optional
        Zero-arg function returning the current accumulated cost.  When
        omitted the guard tracks nothing and always allows requests.
    on_state_change : callable, optional
        ``(old_state, new_state, info_dict) → None`` hook for external
        telemetry / alerting.
    min_backoff_seconds : float
        Floor for the exponential back-off sleep (default 5 s).
    max_backoff_seconds : float
        Ceiling for each individual sleep (default 300 s / 5 min).
    backoff_multiplier : float
        Base multiplier for exponential ramp (default 2.0).
    jitter_fraction : float
        ±fraction of jitter applied to the computed sleep (default 0.25).
    max_wait_window_seconds : float
        Circuit-breaker: total wall-clock seconds to wait before raising
        ``BudgetExceededError`` (default 3 600 s / 1 h).
    """

    def __init__(
        self,
        max_budget: float,
        cost_getter: Optional[Callable[[], float]] = None,
        on_state_change: Optional[
            Callable[[BudgetState, BudgetState, dict], None]
        ] = None,
        *,
        min_backoff_seconds: float = 5.0,
        max_backoff_seconds: float = 300.0,
        backoff_multiplier: float = 2.0,
        jitter_fraction: float = 0.25,
        max_wait_window_seconds: float = 3600.0,
    ):
        self._max_budget = max_budget
        self._cost_getter = cost_getter
        self._on_state_change = on_state_change

        # Backoff tunables
        self._min_backoff = min_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._multiplier = backoff_multiplier
        self._jitter_frac = jitter_fraction
        self._max_wait = max_wait_window_seconds

        # Internal state (guarded by _lock)
        self._state: BudgetState = BudgetState.OK
        self._lock = threading.Lock()
        self._resume_event = threading.Event()
        self._resume_event.set()  # Initially open

        # Reactive: server reported budget exceeded
        self._server_exceeded: bool = False
        self._server_exceeded_info: Optional[dict] = None

        # Telemetry counters
        self._suppressed_requests: int = 0
        self._total_wait_seconds: float = 0.0
        self._backoff_count: int = 0
        self._retry_attempts: int = 0
        self._state_transitions: list[
            tuple[BudgetState, BudgetState, float, dict]
        ] = []

        logger.info(
            "BudgetGuard initialised: max_budget=$%.4f", max_budget
        )

    # -- properties ------------------------------------------------

    @property
    def state(self) -> BudgetState:
        return self._state

    @property
    def max_budget(self) -> float:
        return self._max_budget

    @property
    def current_cost(self) -> float:
        if self._cost_getter:
            return self._cost_getter()
        return 0.0

    @property
    def remaining_budget(self) -> float:
        return max(0.0, self._max_budget - self.current_cost)

    @property
    def is_exceeded(self) -> bool:
        """True when budget is exceeded — either by local cost tracking or
        because the server reported ``budget_exceeded``."""
        if self._server_exceeded:
            return True
        if self._cost_getter:
            return self.current_cost >= self._max_budget
        return False

    @property
    def suppressed_requests(self) -> int:
        return self._suppressed_requests

    @property
    def retry_attempts(self) -> int:
        return self._retry_attempts

    # -- state machine ---------------------------------------------

    def _transition(self, new_state: BudgetState) -> None:
        """Record a state transition (caller must hold ``_lock``)."""
        old = self._state
        if old == new_state:
            return
        self._state = new_state
        info = {
            "current_cost": self.current_cost,
            "max_budget": self._max_budget,
            "remaining": self.remaining_budget,
            "suppressed_requests": self._suppressed_requests,
            "backoff_count": self._backoff_count,
            "server_exceeded": self._server_exceeded,
            "retry_attempts": self._retry_attempts,
        }
        self._state_transitions.append((old, new_state, time.time(), info))
        logger.info(
            "BudgetGuard state: %s → %s | cost=$%.4f/$%.4f | suppressed=%d",
            old.value,
            new_state.value,
            self.current_cost,
            self._max_budget,
            self._suppressed_requests,
        )
        if self._on_state_change:
            try:
                self._on_state_change(old, new_state, info)
            except Exception:
                logger.debug("on_state_change callback error", exc_info=True)

    # -- backoff ---------------------------------------------------

    def _compute_backoff(self) -> float:
        base = self._min_backoff * (self._multiplier ** self._backoff_count)
        capped = min(base, self._max_backoff)
        jitter = random.uniform(-self._jitter_frac, self._jitter_frac) * capped
        return max(self._min_backoff, capped + jitter)

    # -- public API ------------------------------------------------

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Block until budget is available.

        Returns ``True`` when the caller may proceed.
        Raises ``BudgetExceededError`` if the circuit-breaker fires
        (waited longer than *max_wait_window_seconds* or *timeout*).
        """
        max_wait = timeout if timeout is not None else self._max_wait
        wait_start = time.monotonic()

        while True:
            with self._lock:
                if not self.is_exceeded:
                    # Budget is available — transition back to OK
                    if self._state in (
                        BudgetState.WAITING,
                        BudgetState.EXCEEDED,
                    ):
                        self._transition(BudgetState.RESUMED)
                        self._backoff_count = 0
                    elif self._state == BudgetState.RESUMED:
                        # Already logged RESUMED once; normalise to OK
                        self._transition(BudgetState.OK)
                    return True

                # --- budget exceeded ---
                if self._state == BudgetState.OK or self._state == BudgetState.RESUMED:
                    self._transition(BudgetState.EXCEEDED)
                if self._state == BudgetState.EXCEEDED:
                    self._transition(BudgetState.WAITING)

                self._suppressed_requests += 1

            # Circuit-breaker check
            elapsed = time.monotonic() - wait_start
            if elapsed >= max_wait:
                logger.warning(
                    "BudgetGuard circuit-breaker: waited %.1fs (max=%.1fs). "
                    "Suppressed %d requests.",
                    elapsed,
                    max_wait,
                    self._suppressed_requests,
                )
                raise BudgetExceededError(
                    f"Budget exceeded (${self.current_cost:.4f}/"
                    f"${self._max_budget:.4f}) and max wait window of "
                    f"{max_wait:.0f}s reached. "
                    f"Suppressed {self._suppressed_requests} requests."
                )

            # Exponential back-off sleep
            backoff = self._compute_backoff()
            with self._lock:
                self._backoff_count += 1

            logger.info(
                "BudgetGuard WAITING: backoff=%.1fs (attempt %d) | "
                "cost=$%.4f/$%.4f | elapsed=%.1fs/%.1fs",
                backoff,
                self._backoff_count,
                self.current_cost,
                self._max_budget,
                elapsed,
                max_wait,
            )

            remaining_wait = max_wait - elapsed
            sleep_time = min(backoff, remaining_wait)
            if sleep_time > 0:
                # Event can be signalled externally via release_budget()
                self._resume_event.wait(timeout=sleep_time)
                self._resume_event.clear()
                with self._lock:
                    self._total_wait_seconds += sleep_time

    # -- reactive (server-side) API --------------------------------

    _BUDGET_EXCEEDED_PATTERN = re.compile(
        r"budget[_ ]?(has been )?exceeded|budget_exceeded",
        re.IGNORECASE,
    )

    @staticmethod
    def is_budget_error(exc: BaseException) -> bool:
        """Return True if *exc* is a server-side budget-exceeded error.

        Detects the LiteLLM ``budget_exceeded`` response pattern::

            Error code: 400 - {'error': {'message': 'Budget has been
            exceeded! ...', 'type': 'budget_exceeded', ...}}
        """
        msg = str(exc)
        return bool(BudgetGuard._BUDGET_EXCEEDED_PATTERN.search(msg))

    def report_server_exceeded(self, exc: Optional[BaseException] = None) -> None:
        """Signal that the LLM server returned a budget-exceeded error.

        Transitions state to EXCEEDED and causes subsequent ``acquire()``
        calls to block with backoff.
        """
        with self._lock:
            self._server_exceeded = True
            self._server_exceeded_info = {
                "error": str(exc) if exc else "server reported budget exceeded",
                "timestamp": time.time(),
            }
            if self._state in (BudgetState.OK, BudgetState.RESUMED):
                self._transition(BudgetState.EXCEEDED)
            logger.warning(
                "BudgetGuard: server reported budget exceeded. "
                "Entering backoff. error=%s",
                str(exc)[:200] if exc else "(no detail)",
            )

    def clear_server_exceeded(self) -> None:
        """Clear the server-exceeded flag (called after a successful LLM call
        following a previous server-exceeded state)."""
        with self._lock:
            if self._server_exceeded:
                self._server_exceeded = False
                self._server_exceeded_info = None
                logger.info(
                    "BudgetGuard: server budget cleared — LLM call succeeded."
                )

    def release_budget(self, new_max: Optional[float] = None) -> None:
        """Signal that budget is now available (e.g. new billing period).

        Optionally update *max_budget*.  Wakes any threads sleeping in
        ``acquire()``.
        """
        with self._lock:
            if new_max is not None:
                self._max_budget = new_max
            self._server_exceeded = False
            self._server_exceeded_info = None
            self._resume_event.set()
            if not self.is_exceeded:
                self._transition(BudgetState.RESUMED)
                self._backoff_count = 0

    def reset(self, new_max: Optional[float] = None) -> None:
        """Full reset for a new budget cycle.

        Does **not** reset the external cost getter — only internal
        counters.
        """
        with self._lock:
            if new_max is not None:
                self._max_budget = new_max
            self._server_exceeded = False
            self._server_exceeded_info = None
            self._suppressed_requests = 0
            self._total_wait_seconds = 0.0
            self._backoff_count = 0
            self._retry_attempts = 0
            self._state_transitions.clear()
            self._resume_event.set()
            self._transition(BudgetState.OK)

    def get_telemetry(self) -> dict:
        """Return a telemetry snapshot (safe to serialise to JSON)."""
        with self._lock:
            return {
                "state": self._state.value,
                "current_cost": self.current_cost,
                "max_budget": self._max_budget,
                "remaining_budget": self.remaining_budget,
                "suppressed_requests": self._suppressed_requests,
                "retry_attempts": self._retry_attempts,
                "total_wait_seconds": self._total_wait_seconds,
                "backoff_count": self._backoff_count,
                "server_exceeded": self._server_exceeded,
                "server_exceeded_info": self._server_exceeded_info,
                "state_transitions": [
                    {
                        "from": old.value,
                        "to": new.value,
                        "timestamp": ts,
                        "info": info,
                    }
                    for old, new, ts, info in self._state_transitions
                ],
            }
