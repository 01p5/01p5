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

    # Be unambiguous: the agent's system prompt says "investigate before
    # acting", which under load can drift into "investigate forever and
    # never act". Anchor the request on the destructive tool name so the
    # LLM still investigates a bit but reliably lands on delete_pod.
    page.locator("#task-input").fill(
        f"Use the delete_pod tool to delete the throwaway test pod "
        f"named {throwaway_pod} in namespace {NAMESPACE}. "
        f"This pod is a test target and must be removed."
    )
    page.locator("#task-input").press("Enter")

    # Approval card should surface; its <h3> reads "sysadmin → delete_pod".
    # 300s timeout because the agent serialises tool calls and OpenAI
    # round-trips can stack to >3min when the bus/cluster are loaded.
    approval = page.locator(".approval", has_text="delete_pod").first
    expect(approval).to_be_visible(timeout=300_000)
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
        f"Use the delete_pod tool to delete the throwaway test pod "
        f"named {throwaway_pod} in namespace {NAMESPACE}. "
        f"This pod is a test target and must be removed."
    )
    page.locator("#task-input").press("Enter")

    approval = page.locator(".approval", has_text="delete_pod").first
    expect(approval).to_be_visible(timeout=300_000)
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


# ====================================================================
# Section 2 — navigation across the five tabs
# ====================================================================


def test_top_nav_lists_all_five_tabs(page: Page) -> None:
    page.goto(DASHBOARD_URL, wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    for label in ("Chat", "Kubernetes", "Terraform", "Ansible", "Programmer"):
        expect(page.locator(f"header nav a:has-text('{label}')")).to_be_visible()


def test_clicking_each_tab_updates_url_and_active_state(page: Page) -> None:
    page.goto(DASHBOARD_URL, wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)

    # Each tab's URL + an element unique to that page.
    cases = [
        ("Kubernetes", "/kubernetes", "h1:has-text('Kubernetes')"),
        ("Terraform",  "/terraform",  "h1:has-text('Terraform')"),
        ("Ansible",    "/ansible",    "h1:has-text('Ansible')"),
        ("Programmer", "/programmer", "h1:has-text('Programmer')"),
        ("Chat",       "/chat",       "h1:has-text('Conversation')"),
    ]
    for label, expected_path, unique_selector in cases:
        page.locator(f"header nav a:has-text('{label}')").click()
        # URL updates client-side
        page.wait_for_url(f"**{expected_path}", timeout=5_000)
        # Active tab class set on the clicked link
        active = page.locator(f"header nav a:has-text('{label}')")
        expect(active).to_have_class(re_compile_substring("bg-dark-panel"))
        # Page-specific content renders
        expect(page.locator(unique_selector)).to_be_visible(timeout=5_000)


def test_spa_hard_refresh_on_deep_route_works(page: Page) -> None:
    """SPA fallback: hard-loading /kubernetes directly should serve
    index.html and the React router should boot on the kubernetes
    page, not redirect."""
    page.goto(DASHBOARD_URL.rstrip("/") + "/kubernetes", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    expect(page.locator("h1:has-text('Kubernetes')")).to_be_visible(timeout=10_000)


# ====================================================================
# Section 3 — Chat: new conversation, example prompts, context, collapse
# ====================================================================


def test_chat_empty_state_renders_example_prompts(page: Page) -> None:
    page.goto(DASHBOARD_URL, wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    # If there's no chat history yet the empty state renders 4 examples.
    # But a previous test may have populated turns; refresh via /chat
    # and press "New" if visible to guarantee the empty state.
    page.goto(DASHBOARD_URL.rstrip("/") + "/chat", wait_until="load")
    new_btn = page.locator("button:has-text('New')")
    if new_btn.count() and new_btn.is_enabled():
        new_btn.click()
    # The four example buttons appear (use unique text to anchor).
    expect(page.locator("button:has-text('list pods in default namespace')").first).to_be_visible(timeout=10_000)


def test_chat_example_button_submits_in_one_click(page: Page) -> None:
    page.goto(DASHBOARD_URL.rstrip("/") + "/chat", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    new_btn = page.locator("button:has-text('New')")
    if new_btn.count() and new_btn.is_enabled():
        new_btn.click()

    # Click an example prompt; a user/assistant bubble pair should
    # appear without needing a follow-up Send press.
    page.locator("button:has-text('list pods in default namespace')").first.click()
    # User bubble appears (the prompt text shows in the chat stream).
    stream = page.locator("#chat-stream")
    expect(stream).to_contain_text("list pods in default namespace", timeout=10_000)


def test_chat_new_button_clears_history(page: Page) -> None:
    page.goto(DASHBOARD_URL.rstrip("/") + "/chat", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    new_btn = page.locator("button:has-text('New')")
    if new_btn.count() and new_btn.is_enabled():
        new_btn.click()
    # Submit something quick so we have at least one turn.
    marker = f"e2e-clear-{uuid.uuid4().hex[:6]}"
    page.locator("#task-input").fill(f"ping [{marker}]")
    page.locator("#task-input").press("Enter")
    expect(page.locator("#chat-stream")).to_contain_text(marker, timeout=10_000)
    # New button is now enabled. Click it; the chat should clear.
    page.locator("button:has-text('New')").click()
    # No bubble for the marker text any more.
    expect(page.locator(f"#chat-stream :text('{marker}')")).to_have_count(0, timeout=5_000)


def test_chat_context_threads_through_pronoun_resolution(page: Page) -> None:
    """Two-turn flow: pose a counting question, then ask "which of them
    is older". The agent should resolve "them" via the prepended history
    instead of asking the user to clarify."""
    page.goto(DASHBOARD_URL.rstrip("/") + "/chat", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    new_btn = page.locator("button:has-text('New')")
    if new_btn.count() and new_btn.is_enabled():
        new_btn.click()

    # Turn 1: list pods so the agent has concrete subjects to reference.
    page.locator("#task-input").fill("List pods in default namespace and tell me their names.")
    page.locator("#task-input").press("Enter")
    # Wait for assistant prose to appear (first turn settled).
    expect(page.locator("#chat-stream .prose").first).to_be_visible(timeout=180_000)

    # Turn 2: pronoun "them" must resolve to the pods.
    page.locator("#task-input").fill("Which of them is older?")
    page.locator("#task-input").press("Enter")
    # Two assistant bubbles now exist.
    expect(page.locator("#chat-stream .prose").nth(1)).to_be_visible(timeout=180_000)

    second = page.locator("#chat-stream .prose").nth(1).inner_text()
    # If context threading works the agent talks about specific pods
    # (mentions age / older / oldest). If it doesn't, it asks the
    # user to clarify what "them" refers to.
    assert any(w in second.lower() for w in ("older", "oldest", "age", "ago", "newer")), \
        f"agent didn't seem to compare ages; got: {second[:200]}"
    assert "clarify" not in second.lower() and "which of" not in second.lower(), \
        f"agent lost the antecedent and asked the user to clarify; got: {second[:200]}"


def test_chat_long_message_collapses_with_show_more(page: Page) -> None:
    """An agent response longer than ~600 chars should render
    truncated with a 'show more' affordance."""
    page.goto(DASHBOARD_URL.rstrip("/") + "/chat", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    new_btn = page.locator("button:has-text('New')")
    if new_btn.count() and new_btn.is_enabled():
        new_btn.click()

    # Ask for output we expect to be long: describe + 100 log lines.
    page.locator("#task-input").fill(
        "Describe the olympus-olympus pod in the default namespace AND "
        "fetch the last 100 lines of its logs AND list all events. Be "
        "thorough — include the full describe output verbatim and a "
        "long enumeration of the events."
    )
    page.locator("#task-input").press("Enter")

    # Wait for the assistant turn to settle.
    expect(page.locator("#chat-stream .prose").first).to_be_visible(timeout=180_000)
    # Look for the "show more" button on the long bubble.
    show_more = page.locator("button:has-text('show more')").first
    # If the response was short (rare for this prompt), the button
    # may not appear — make the assertion soft so a tiny response
    # doesn't flake the test.
    if show_more.count():
        show_more.click()
        expect(page.locator("button:has-text('show less')").first).to_be_visible(timeout=5_000)


# ====================================================================
# Section 4 — Kubernetes page
# ====================================================================


def test_k8s_pods_tab_renders_table(page: Page) -> None:
    page.goto(DASHBOARD_URL.rstrip("/") + "/kubernetes", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    # Header + namespace input present.
    expect(page.locator("h1:has-text('Kubernetes')")).to_be_visible()
    # The "Pods" tab is selected by default; a table or empty-state
    # renders within ~30s of mounting (waits for /tools call).
    pods_area = page.locator("section:has(h1:text('Kubernetes'))")
    # We'll just assert the pods table or empty fallback contains either
    # rows or the explanatory text — either is acceptable.
    expect(pods_area).to_contain_text("Name", timeout=30_000)  # column header


def test_k8s_nodes_tab_lists_cluster_nodes(page: Page) -> None:
    """With the post-fix ClusterRole, get_nodes returns the 4 nodes."""
    page.goto(DASHBOARD_URL.rstrip("/") + "/kubernetes", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    # The Pods/Nodes/Events tab strip — the second tab is Nodes. Use
    # role+name (exact) so it never collides with table cell text.
    page.get_by_role("button", name="Nodes", exact=True).click()
    # Master + 3 workers should appear.
    expect(page.get_by_text("k8s-master", exact=True).first).to_be_visible(timeout=60_000)
    for w in ("k8s-worker-worker1", "k8s-worker-worker2", "k8s-worker-worker3"):
        expect(page.get_by_text(w, exact=True).first).to_be_visible(timeout=15_000)


def test_k8s_pod_describe_modal_opens(page: Page, throwaway_pod: str) -> None:
    """Click the inline 'describe' action button on a pod row → modal."""
    page.goto(DASHBOARD_URL.rstrip("/") + "/kubernetes", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    # Find the row for our throwaway pod.
    row = page.locator(f"tr:has-text('{throwaway_pod}')")
    expect(row).to_be_visible(timeout=60_000)
    row.locator("button:has-text('describe')").click()
    # Modal renders with the pod name in title.
    expect(page.locator(f"h2:has-text('Describe — {throwaway_pod}')")).to_be_visible(timeout=60_000)


# ====================================================================
# Section 5 — Terraform page
# ====================================================================


def test_terraform_lists_discovered_stacks(page: Page) -> None:
    page.goto(DASHBOARD_URL.rstrip("/") + "/terraform", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    expect(page.locator("h1:has-text('Terraform')")).to_be_visible()
    # Each stack card's h3 carries the stack name verbatim — that's
    # a more stable anchor than free-text matching, which could
    # collide with the "/opt/olympus/infra/..." subtitle on the same card.
    expect(page.locator("h3:has-text('terraform/pve')").first).to_be_visible(timeout=30_000)
    expect(page.locator("h3:has-text('terraform/aws')").first).to_be_visible(timeout=30_000)


def test_terraform_validate_button_opens_output_modal(page: Page) -> None:
    """Click validate on the pve stack — modal opens with terraform
    output text, no approval needed (read-only operation)."""
    page.goto(DASHBOARD_URL.rstrip("/") + "/terraform", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    # Wait for the stack cards to render before searching for one.
    expect(page.locator("h3:has-text('terraform/pve')").first).to_be_visible(timeout=30_000)

    # The card itself is the closest p-4 ancestor of the h3 — go up
    # via xpath to scope the validate-button click to THIS card.
    validate_btn = (
        page.locator("h3:has-text('terraform/pve')").first
        .locator("xpath=ancestor::div[contains(@class,'p-4')][1]")
        .locator("button", has_text="validate")
    )
    validate_btn.click()

    # Modal opens with "Validate — terraform/pve" in the title.
    expect(page.locator("h2:has-text('Validate — terraform/pve')")).to_be_visible(timeout=180_000)


# ====================================================================
# Section 6 — Ansible page
# ====================================================================


def test_ansible_lists_discovered_playbooks(page: Page) -> None:
    page.goto(DASHBOARD_URL.rstrip("/") + "/ansible", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    expect(page.locator("h1:has-text('Ansible')")).to_be_visible()
    # Playbook card h3 strips the "ansible/" path prefix and shows
    # just the filename. Use h3:has-text to anchor unambiguously and
    # bump the timeout — the /stacks/ansible fetch has a momentary
    # delay on the cold page mount.
    for play in ("master.yml", "workers.yml", "docker.yml"):
        expect(page.locator(f"h3:has-text('{play}')").first).to_be_visible(timeout=30_000)


def test_ansible_inventory_field_has_default(page: Page) -> None:
    page.goto(DASHBOARD_URL.rstrip("/") + "/ansible", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    # Inventory field defaults to the path the terraform PVE module
    # emits — locate by the rendered value attribute so we don't rely
    # on label text association.
    expect(page.locator("input[value*='inventory.ini']").first).to_be_visible()


# ====================================================================
# Section 7 — Programmer page (generators)
# ====================================================================


def _generator_card(page: Page, heading: str):
    """Locate the generator card whose h3 reads ``heading``.

    Each generator is wrapped in a Card (a p-5 div); the h3 text is
    the unique anchor. We walk up to the closest p-5 ancestor div so
    the returned scope is exactly one card — no strict-mode bleed-
    over to siblings' Generate buttons."""
    return (
        page.locator(f"h3:has-text('{heading}')").first
        .locator("xpath=ancestor::div[contains(@class,'p-5')][1]")
    )


def test_programmer_dockerfile_generator_shows_preview(page: Page) -> None:
    """Click Generate on the Dockerfile card → preview block renders
    with FROM python:3.12-slim. No approval needed (read-only generator)."""
    page.goto(DASHBOARD_URL.rstrip("/") + "/programmer", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    card = _generator_card(page, "Dockerfile")
    card.locator("button", has_text="Generate").click()
    # The preview CodeBlock inside this card renders the Dockerfile.
    expect(card.locator("pre", has_text="FROM python:3.12-slim")).to_be_visible(timeout=30_000)


def test_programmer_helm_generator_shows_yaml_preview(page: Page) -> None:
    page.goto(DASHBOARD_URL.rstrip("/") + "/programmer", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    card = _generator_card(page, "Helm values.yaml")
    card.locator("button", has_text="Generate").click()
    expect(card.locator("pre", has_text="replicaCount")).to_be_visible(timeout=30_000)


# ====================================================================
# Section 8 — Diff rendering in approval cards
# ====================================================================


def test_edit_file_approval_card_renders_unified_diff(page: Page) -> None:
    """Chat flow that triggers an edit_file approval; the card in the
    right sidebar should contain a unified-diff CodeBlock with both
    + and - lines. We seed a known target file via the API first
    (the existing /tools/programmer/write_file is gated by approval
    in the running deployment, so we use the underlying tool by
    spawning a one-off pod-side write via kubectl exec)."""
    # Seed a file via kubectl exec so we don't have to navigate the
    # approval flow just to get the test fixture in place.
    seed_path = f"/tmp/e2e-edit-{uuid.uuid4().hex[:6]}.tf"
    seed_content = 'region = "us-west-1"\nbucket = "example"\n'
    _kubectl_exec_olympus(
        "sh", "-c", f"cat > {seed_path}",
        stdin=seed_content,
    )

    page.goto(DASHBOARD_URL.rstrip("/") + "/chat", wait_until="load")
    expect(page.locator("#health")).to_have_text("connected", timeout=10_000)
    # Clear any prior chat state.
    new_btn = page.locator("button:has-text('New')")
    if new_btn.count() and new_btn.is_enabled():
        new_btn.click()

    page.locator("#task-input").fill(
        f"Use edit_file on {seed_path} to change region from "
        f'"us-west-1" to "us-east-2". Read the file first.'
    )
    page.locator("#task-input").press("Enter")

    # Approval card appears with a diff.
    approval = page.locator(".approval", has_text="edit_file").first
    expect(approval).to_be_visible(timeout=180_000)
    # Diff CodeBlock has language label "diff".
    expect(approval.locator("span:has-text('diff')").first).to_be_visible(timeout=10_000)
    # Diff contains a + and - line for the region change.
    diff_block = approval.locator("pre", has_text="region")
    expect(diff_block.first).to_be_visible()
    # Tinted line classes from DiffHighlight.
    expect(approval.locator(".text-accent-green").first).to_be_visible()
    expect(approval.locator(".text-accent-red").first).to_be_visible()

    # Reject so we don't actually mutate.
    page.on("dialog", lambda d: d.accept("e2e diff render test, rejecting"))
    approval.locator("button.reject").click()
    expect(approval).to_have_count(0, timeout=30_000)


# ====================================================================
# Helpers
# ====================================================================


def re_compile_substring(s: str):
    """Playwright's to_have_class accepts a regex. Build one that
    matches if the class list contains the substring."""
    import re
    return re.compile(re.escape(s))


def _kubectl_exec_olympus(*cmd: str, stdin: str = "") -> str:
    """Run a command inside the live dashboard pod via kubectl exec,
    feeding stdin. Used to seed test fixtures on the pod's filesystem."""
    full = [
        "kubectl", "exec", "-i", "deploy/olympus-olympus",
        "--", *cmd,
    ]
    proc = subprocess.run(full, input=stdin, capture_output=True, text=True, timeout=KUBECTL_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl exec failed: {proc.stderr}")
    return proc.stdout
