"""
Tests for BudgetGuard — budget-aware gating for LLM calls.

Covers both proactive (local cost_getter) and reactive (server-side
budget_exceeded error) modes.

Run with:
    python -m pytest tests/test_budget_guard.py -v
"""

from __future__ import annotations

import threading
import time
import unittest

from agentlib.budget import BudgetExceededError, BudgetGuard, BudgetState


class _MutableCost:
    """Helper: thread-safe mutable cost counter."""

    def __init__(self, initial: float = 0.0):
        self._cost = initial
        self._lock = threading.Lock()

    def get(self) -> float:
        with self._lock:
            return self._cost

    def set(self, value: float) -> None:
        with self._lock:
            self._cost = value

    def add(self, delta: float) -> None:
        with self._lock:
            self._cost += delta


# ------------------------------------------------------------------
# Core acceptance-criteria tests
# ------------------------------------------------------------------


class TestBudgetExceededBlocksRequests(unittest.TestCase):
    """AC: When budget is exceeded the agent stops sending LLM requests."""

    def test_acquire_blocks_when_exceeded(self):
        """acquire() must NOT return while cost ≥ max_budget."""
        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=0.05,
            max_backoff_seconds=0.1,
            max_wait_window_seconds=0.5,
        )

        with self.assertRaises(BudgetExceededError):
            guard.acquire(timeout=0.3)

        # Suppressed requests must be > 0
        self.assertGreater(guard.suppressed_requests, 0)
        # State must be WAITING
        self.assertEqual(guard.state, BudgetState.WAITING)

    def test_zero_outgoing_requests_while_waiting(self):
        """No LLM call must go through while budget is exceeded."""
        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=0.05,
            max_backoff_seconds=0.1,
            max_wait_window_seconds=0.5,
        )

        call_count = 0

        def fake_llm_call():
            nonlocal call_count
            guard.acquire(timeout=0.3)
            call_count += 1  # should never reach here

        with self.assertRaises(BudgetExceededError):
            fake_llm_call()

        self.assertEqual(call_count, 0, "LLM calls must be 0 while budget exceeded")


class TestBudgetRestoredResumesRequests(unittest.TestCase):
    """AC: When budget becomes available the agent resumes without manual
    intervention."""

    def test_acquire_unblocks_when_cost_drops(self):
        """acquire() must return True once cost drops below max_budget."""
        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=0.05,
            max_backoff_seconds=0.1,
            max_wait_window_seconds=5.0,
        )

        result = [None]
        error = [None]

        def worker():
            try:
                result[0] = guard.acquire()
            except Exception as exc:
                error[0] = exc

        t = threading.Thread(target=worker)
        t.start()

        # Let the guard sleep once, then lower cost
        time.sleep(0.15)
        cost.set(2.0)
        t.join(timeout=3.0)

        self.assertIsNone(error[0], f"Unexpected error: {error[0]}")
        self.assertTrue(result[0])
        self.assertIn(
            guard.state,
            (BudgetState.RESUMED, BudgetState.OK),
        )

    def test_release_budget_wakes_waiters(self):
        """release_budget() should immediately wake threads blocked in
        acquire()."""
        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=5.0,  # long backoff
            max_backoff_seconds=10.0,
            max_wait_window_seconds=30.0,
        )

        acquired = threading.Event()

        def worker():
            guard.acquire()
            acquired.set()

        t = threading.Thread(target=worker)
        t.start()

        time.sleep(0.1)
        # Lower cost and signal
        cost.set(0.0)
        guard.release_budget()
        self.assertTrue(
            acquired.wait(timeout=2.0),
            "acquire() must unblock after release_budget()",
        )
        t.join(timeout=1.0)


# ------------------------------------------------------------------
# No tight retry loop
# ------------------------------------------------------------------


class TestNoTightRetryLoop(unittest.TestCase):
    """AC: No tight retry loop under any configuration."""

    def test_minimum_backoff_enforced(self):
        """Even with min settings, sleep interval ≥ min_backoff_seconds."""
        cost = _MutableCost(10.0)
        min_bo = 0.05
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=min_bo,
            max_backoff_seconds=0.1,
            max_wait_window_seconds=0.4,
        )

        start = time.monotonic()
        with self.assertRaises(BudgetExceededError):
            guard.acquire(timeout=0.25)
        elapsed = time.monotonic() - start

        # Should have waited at least one backoff interval
        self.assertGreaterEqual(elapsed, min_bo)

    def test_backoff_increases(self):
        """Backoff must grow (exponentially) across retries."""
        guard = BudgetGuard(
            max_budget=0,
            cost_getter=lambda: 1.0,
            min_backoff_seconds=1.0,
            max_backoff_seconds=1000.0,
            backoff_multiplier=2.0,
            jitter_fraction=0.0,  # deterministic
        )
        b0 = guard._compute_backoff()
        guard._backoff_count = 1
        b1 = guard._compute_backoff()
        guard._backoff_count = 2
        b2 = guard._compute_backoff()

        self.assertGreater(b1, b0)
        self.assertGreater(b2, b1)


# ------------------------------------------------------------------
# State-transition telemetry
# ------------------------------------------------------------------


class TestTelemetry(unittest.TestCase):
    """AC: Telemetry clearly shows state transitions and request suppression."""

    def test_state_transitions_recorded(self):
        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=0.02,
            max_backoff_seconds=0.05,
            max_wait_window_seconds=0.2,
        )

        with self.assertRaises(BudgetExceededError):
            guard.acquire(timeout=0.15)

        tel = guard.get_telemetry()
        self.assertIn("state_transitions", tel)
        self.assertGreater(len(tel["state_transitions"]), 0)
        # First transition should be OK → EXCEEDED
        first = tel["state_transitions"][0]
        self.assertEqual(first["from"], BudgetState.OK.value)
        self.assertEqual(first["to"], BudgetState.EXCEEDED.value)

    def test_on_state_change_callback(self):
        transitions = []

        def recorder(old, new, info):
            transitions.append((old, new))

        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            on_state_change=recorder,
            min_backoff_seconds=0.02,
            max_backoff_seconds=0.05,
            max_wait_window_seconds=0.2,
        )

        with self.assertRaises(BudgetExceededError):
            guard.acquire(timeout=0.15)

        self.assertGreater(len(transitions), 0)
        self.assertEqual(transitions[0], (BudgetState.OK, BudgetState.EXCEEDED))

    def test_telemetry_snapshot_fields(self):
        guard = BudgetGuard(max_budget=10.0, cost_getter=lambda: 0.0)
        tel = guard.get_telemetry()
        for key in (
            "state",
            "current_cost",
            "max_budget",
            "remaining_budget",
            "suppressed_requests",
            "total_wait_seconds",
            "backoff_count",
            "state_transitions",
        ):
            self.assertIn(key, tel)


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


class TestEdgeCases(unittest.TestCase):

    def test_no_budget_guard_passthrough(self):
        """When budget_guard is None, acquire is never called."""
        # This tests the StructuralAgent integration indirectly —
        # the guard being None must not raise.
        guard = None
        # Simulate the gate check from StructuralAgent.invoke
        if guard is not None:
            guard.acquire()
        # No error → pass

    def test_budget_not_exceeded_returns_immediately(self):
        guard = BudgetGuard(max_budget=100.0, cost_getter=lambda: 1.0)
        start = time.monotonic()
        result = guard.acquire()
        elapsed = time.monotonic() - start
        self.assertTrue(result)
        self.assertLess(elapsed, 0.1)

    def test_reset_clears_counters(self):
        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=0.02,
            max_backoff_seconds=0.05,
            max_wait_window_seconds=0.2,
        )

        with self.assertRaises(BudgetExceededError):
            guard.acquire(timeout=0.1)

        self.assertGreater(guard.suppressed_requests, 0)

        cost.set(0.0)
        guard.reset(new_max=100.0)

        self.assertEqual(guard.suppressed_requests, 0)
        self.assertEqual(guard.state, BudgetState.OK)
        self.assertEqual(guard.max_budget, 100.0)

    def test_concurrent_acquires(self):
        """Multiple threads should all be gated properly."""
        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=0.05,
            max_backoff_seconds=0.1,
            max_wait_window_seconds=5.0,
        )

        results = [None] * 4
        errors = [None] * 4

        def worker(idx):
            try:
                results[idx] = guard.acquire()
            except Exception as exc:
                errors[idx] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()

        # Let them all block, then free budget
        time.sleep(0.2)
        cost.set(0.0)
        guard.release_budget()

        for t in threads:
            t.join(timeout=3.0)

        for i in range(4):
            self.assertIsNone(errors[i], f"Thread {i} error: {errors[i]}")
            self.assertTrue(results[i])

    def test_release_budget_with_new_max(self):
        cost = _MutableCost(10.0)
        guard = BudgetGuard(
            max_budget=5.0,
            cost_getter=cost.get,
            min_backoff_seconds=5.0,
            max_backoff_seconds=10.0,
            max_wait_window_seconds=30.0,
        )

        acquired = threading.Event()

        def worker():
            guard.acquire()
            acquired.set()

        t = threading.Thread(target=worker)
        t.start()

        time.sleep(0.1)
        # Raise the budget ceiling instead of lowering cost
        guard.release_budget(new_max=20.0)
        self.assertTrue(
            acquired.wait(timeout=2.0),
            "acquire() must unblock when max_budget is raised above cost",
        )
        t.join(timeout=1.0)
        self.assertEqual(guard.max_budget, 20.0)


# ------------------------------------------------------------------
# Reactive (server-side) budget detection
# ------------------------------------------------------------------

# Real error string from LiteLLM logs
_LITELLM_BUDGET_ERROR = (
    "Error code: 400 - {'error': {'message': "
    "'Budget has been exceeded! Current cost: 0.94636259, Max budget: 0.0', "
    "'type': 'budget_exceeded', 'param': None, 'code': '400'}}"
)


class TestIsBudgetError(unittest.TestCase):
    """BudgetGuard.is_budget_error() must detect server errors."""

    def test_detects_litellm_budget_exceeded(self):
        exc = Exception(_LITELLM_BUDGET_ERROR)
        self.assertTrue(BudgetGuard.is_budget_error(exc))

    def test_detects_type_budget_exceeded(self):
        exc = Exception("type: budget_exceeded")
        self.assertTrue(BudgetGuard.is_budget_error(exc))

    def test_detects_budget_has_been_exceeded(self):
        exc = Exception("Budget has been exceeded!")
        self.assertTrue(BudgetGuard.is_budget_error(exc))

    def test_does_not_match_unrelated_400(self):
        exc = Exception("Error code: 400 - {'error': {'message': 'Invalid model'}}")
        self.assertFalse(BudgetGuard.is_budget_error(exc))

    def test_does_not_match_generic_error(self):
        exc = ValueError("something went wrong")
        self.assertFalse(BudgetGuard.is_budget_error(exc))


class TestReportServerExceeded(unittest.TestCase):
    """Reactive: report_server_exceeded() must gate subsequent acquire() calls."""

    def test_server_exceeded_blocks_acquire(self):
        """After report_server_exceeded(), acquire() must block."""
        guard = BudgetGuard(
            max_budget=999.0,  # local cost is fine
            cost_getter=lambda: 0.0,
            min_backoff_seconds=0.05,
            max_backoff_seconds=0.1,
            max_wait_window_seconds=0.5,
        )

        exc = Exception(_LITELLM_BUDGET_ERROR)
        guard.report_server_exceeded(exc)

        self.assertTrue(guard.is_exceeded)
        self.assertEqual(guard.state, BudgetState.EXCEEDED)

        # acquire() should block and eventually circuit-break
        with self.assertRaises(BudgetExceededError):
            guard.acquire(timeout=0.3)

        self.assertGreater(guard.suppressed_requests, 0)

    def test_server_exceeded_zero_calls_while_waiting(self):
        """No LLM calls go through while server-exceeded is set."""
        guard = BudgetGuard(
            max_budget=999.0,
            cost_getter=lambda: 0.0,
            min_backoff_seconds=0.05,
            max_backoff_seconds=0.1,
            max_wait_window_seconds=0.5,
        )
        guard.report_server_exceeded(Exception(_LITELLM_BUDGET_ERROR))

        call_count = 0

        def fake_llm_call():
            nonlocal call_count
            guard.acquire(timeout=0.2)
            call_count += 1

        with self.assertRaises(BudgetExceededError):
            fake_llm_call()

        self.assertEqual(call_count, 0, "LLM calls must be 0 while server-exceeded")


class TestClearServerExceeded(unittest.TestCase):
    """clear_server_exceeded() allows requests to resume."""

    def test_clear_unblocks_acquire(self):
        guard = BudgetGuard(
            max_budget=999.0,
            cost_getter=lambda: 0.0,
            min_backoff_seconds=5.0,
            max_backoff_seconds=10.0,
            max_wait_window_seconds=30.0,
        )
        guard.report_server_exceeded(Exception("budget exceeded"))

        acquired = threading.Event()

        def worker():
            guard.acquire()
            acquired.set()

        t = threading.Thread(target=worker)
        t.start()

        time.sleep(0.1)
        guard.clear_server_exceeded()
        guard.release_budget()  # wake sleepers
        self.assertTrue(
            acquired.wait(timeout=2.0),
            "acquire() must unblock after clear_server_exceeded()",
        )
        t.join(timeout=1.0)

    def test_release_budget_clears_server_exceeded(self):
        guard = BudgetGuard(
            max_budget=999.0,
            cost_getter=lambda: 0.0,
            min_backoff_seconds=5.0,
            max_backoff_seconds=10.0,
            max_wait_window_seconds=30.0,
        )
        guard.report_server_exceeded(Exception("budget exceeded"))

        acquired = threading.Event()

        def worker():
            guard.acquire()
            acquired.set()

        t = threading.Thread(target=worker)
        t.start()

        time.sleep(0.1)
        # release_budget should also clear server_exceeded
        guard.release_budget()
        self.assertTrue(
            acquired.wait(timeout=2.0),
            "release_budget() must clear server_exceeded and unblock acquire()",
        )
        t.join(timeout=1.0)
        self.assertFalse(guard._server_exceeded)


class TestReactiveRetryTelemetry(unittest.TestCase):
    """Telemetry captures server-exceeded info."""

    def test_telemetry_includes_server_exceeded(self):
        guard = BudgetGuard(
            max_budget=999.0,
            cost_getter=lambda: 0.0,
            min_backoff_seconds=0.02,
            max_backoff_seconds=0.05,
            max_wait_window_seconds=0.2,
        )
        guard.report_server_exceeded(Exception(_LITELLM_BUDGET_ERROR))

        with self.assertRaises(BudgetExceededError):
            guard.acquire(timeout=0.1)

        tel = guard.get_telemetry()
        self.assertTrue(tel["server_exceeded"])
        self.assertIsNotNone(tel["server_exceeded_info"])
        self.assertIn("budget_exceeded", tel["server_exceeded_info"]["error"])

    def test_state_transitions_show_exceeded_from_server(self):
        guard = BudgetGuard(
            max_budget=999.0,
            cost_getter=lambda: 0.0,
            min_backoff_seconds=0.02,
            max_backoff_seconds=0.05,
            max_wait_window_seconds=0.2,
        )
        guard.report_server_exceeded(Exception(_LITELLM_BUDGET_ERROR))

        with self.assertRaises(BudgetExceededError):
            guard.acquire(timeout=0.1)

        tel = guard.get_telemetry()
        transitions = tel["state_transitions"]
        self.assertGreater(len(transitions), 0)
        # report_server_exceeded should have triggered OK → EXCEEDED
        self.assertEqual(transitions[0]["from"], BudgetState.OK.value)
        self.assertEqual(transitions[0]["to"], BudgetState.EXCEEDED.value)
        # The info dict should include server_exceeded flag
        self.assertTrue(transitions[0]["info"]["server_exceeded"])


if __name__ == "__main__":
    unittest.main()
