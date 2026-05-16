"""
E2E browser tests for the Olympus dashboard.

Drives a real headless Chromium against a live dashboard via Playwright.
Each test exercises the actual UI surface a human would touch — typing
in the task input, watching the events stream, clicking the approval
buttons — rather than the JSON API the other test files cover.

Opt-in: set ``OLYMPUS_LIVE_E2E=1`` and (optionally)
``OLYMPUS_DASHBOARD_URL`` (defaults to http://10.0.10.30/).

Skipped in CI by default. Run locally on the dev VM (or any host with
Playwright + Chromium installed):

    OLYMPUS_LIVE_E2E=1 pytest agents/dashboard/tests/test_dashboard_e2e.py -v

Requires ``KUBECONFIG`` to point at the live cluster for tests that
spawn / verify pods. Tests are isolated — each one creates and tears
down its own throwaway target pods. If a test fails mid-flight the
``e2e-target`` label gives you a foothold to clean up by hand.
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from collections.abc import Generator

import pytest

playwright_pkg = pytest.importorskip("playwright.sync_api")

if os.environ.get("OLYMPUS_LIVE_E2E") != "1":
    pytest.skip(
        "set OLYMPUS_LIVE_E2E=1 to run end-to-end browser tests against a "
        "live Olympus dashboard",
        allow_module_level=True,
    )

from playwright.sync_api import Browser, Page, expect, sync_playwright  # noqa: E402

DASHBOARD_URL = os.environ.get("OLYMPUS_DASHBOARD_URL", "http://10.0.10.30/")
NAMESPACE = os.environ.get("OLYMPUS_E2E_NAMESPACE", "default")
KUBECTL_TIMEOUT = 20


def _kubectl(*args: str) -> str:
    """Run kubectl against the cluster and return stdout. Raises on
    non-zero. KUBECONFIG must be set in the test runner's env."""
    proc = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=KUBECTL_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"kubectl {' '.join(args)} failed: {proc.stderr.strip()}"
        )
    return proc.stdout


@pytest.fixture(scope="session")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser) -> Generator[Page, None, None]:
    ctx = browser.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture
def throwaway_pod() -> Generator[str, None, None]:
    """Spawn a throwaway test pod, yield its name, clean up after."""
    suffix = uuid.uuid4().hex[:6]
    name = f"e2e-target-{suffix}"
    _kubectl(
        "run", name,
        "--image=nginx:alpine",
        "--restart=Never",
        "--labels=e2e-target=true",
        f"--namespace={NAMESPACE}",
    )
    # Wait for the pod to be Running so the agent sees it as a live target.
    deadline = time.time() + 60
    while time.time() < deadline:
        out = _kubectl(
            "get", "pod", name,
            f"--namespace={NAMESPACE}",
            "-o", "jsonpath={.status.phase}",
        ).strip()
        if out == "Running":
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"pod {name} did not become Running in 60s")
    yield name
    # Best-effort teardown.
    try:
        _kubectl(
            "delete", "pod", name,
            f"--namespace={NAMESPACE}",
            "--ignore-not-found=true",
            "--grace-period=0", "--force",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_index_loads_and_health_connects(page: Page) -> None:
    """Page renders, health pill flips from 'connecting…' to 'connected'
    after the first /healthz round-trip."""
    page.goto(DASHBOARD_URL, wait_until="load")
    expect(page).to_have_title("Olympus dashboard")
    health = page.locator("#health")
    expect(health).to_have_text("connected", timeout=10_000)
    # Task input + submit button are present and enabled.
    expect(page.locator("#task-input")).to_be_visible()
    expect(page.locator("#task-submit")).to_be_enabled()


def test_submit_task_via_form_lands_in_events_feed(page: Page) -> None:
    """Type a read-only NL task in the input, press Enter, watch the
    bus-event row appear in the live #events feed.

    The dashboard replays the bus history on connect, so a freshly-
    loaded page may already show rows from prior tasks. We anchor on
    a *unique* phrase in the submitted NL so the assertion is about
    THIS submission, not the residual history.
    """
    page.goto(DASHBOARD_URL, wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)

    marker = f"e2e-marker-{uuid.uuid4().hex[:8]}"
    page.locator("#task-input").fill(
        f"list pods in default namespace [{marker}]"
    )
    page.locator("#task-input").press("Enter")

    marked_event = page.locator("#events .event", has_text=marker).first
    expect(marked_event).to_be_visible(timeout=30_000)
    # The marked row's leading kind chip should be "task".
    expect(marked_event.locator(".kind")).to_have_text("task")


def test_destructive_task_surfaces_approval_card_and_approve_deletes_pod(
    page: Page, throwaway_pod: str
) -> None:
    """Full destructive round-trip via the UI: submit delete task, see
    approval card render with the right tool + args, click Approve in
    the browser, confirm the pod is gone via kubectl."""
    page.goto(DASHBOARD_URL, wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)

    # Browser's window.prompt() pops on Approve/Reject; auto-fill it.
    page.on("dialog", lambda d: d.accept("e2e approve"))

    page.locator("#task-input").fill(
        f"Delete the pod named {throwaway_pod} in the {NAMESPACE} namespace."
    )
    page.locator("#task-input").press("Enter")

    # Approval card should surface; its <h3> reads "sysadmin → delete_pod".
    approval = page.locator(".approval", has_text="delete_pod").first
    expect(approval).to_be_visible(timeout=120_000)
    # Tool args block must mention our throwaway pod.
    expect(approval.locator("pre", has_text=throwaway_pod).first).to_be_visible()

    approval.locator("button.approve").click()

    # Wait for the approval card to disappear AND the pod to be deleted.
    expect(approval).to_have_count(0, timeout=60_000)

    deadline = time.time() + 60
    while time.time() < deadline:
        out = subprocess.run(
            ["kubectl", "get", "pod", throwaway_pod,
             f"--namespace={NAMESPACE}", "--ignore-not-found=true",
             "-o", "name"],
            capture_output=True, text=True, timeout=KUBECTL_TIMEOUT,
        ).stdout.strip()
        if not out:
            return  # success — pod is gone
        time.sleep(2)
    raise AssertionError(
        f"pod {throwaway_pod} still exists 60s after approval clicked"
    )


def test_destructive_task_reject_preserves_pod(
    page: Page, throwaway_pod: str
) -> None:
    """Same flow but click Reject — the underlying tool must NOT fire."""
    page.goto(DASHBOARD_URL, wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)

    page.on("dialog", lambda d: d.accept("e2e reject"))

    page.locator("#task-input").fill(
        f"Delete the pod named {throwaway_pod} in the {NAMESPACE} namespace."
    )
    page.locator("#task-input").press("Enter")

    approval = page.locator(".approval", has_text="delete_pod").first
    expect(approval).to_be_visible(timeout=120_000)
    approval.locator("button.reject").click()

    # Card disappears (resolved).
    expect(approval).to_have_count(0, timeout=60_000)

    # Pod should still be alive 10s later — the runtime should have
    # returned "REJECTED" without ever calling kubectl delete.
    time.sleep(10)
    out = subprocess.run(
        ["kubectl", "get", "pod", throwaway_pod,
         f"--namespace={NAMESPACE}", "-o", "jsonpath={.status.phase}"],
        capture_output=True, text=True, timeout=KUBECTL_TIMEOUT,
    ).stdout.strip()
    assert out == "Running", (
        f"pod {throwaway_pod} status was {out!r} after reject — "
        f"expected to be alive"
    )


def test_audit_log_panel_renders_recent_calls(page: Page) -> None:
    """After running any tool above, the audit panel should be
    non-empty — we don't pin specific rows because the live log
    churns, just that something is there."""
    page.goto(DASHBOARD_URL, wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)

    # Fire a read-only task to guarantee at least one audit row exists
    # in the current pod's emptyDir-backed log.
    page.locator("#task-input").fill("list pods in default namespace")
    page.locator("#task-input").press("Enter")

    # Audit panel polls every 3s; give it two cycles.
    audit_rows = page.locator("#audit .audit-row")
    expect(audit_rows.first).to_be_visible(timeout=30_000)
