import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalsPanel } from "./ApprovalsPanel";
import { api } from "../api";
import type { PendingApproval } from "../types";

const APPROVALS: PendingApproval[] = [
  {
    approval_id: "ap-1",
    agent: "sysadmin",
    tool: "delete_pod",
    args: { name: "foo", namespace: "default" },
    rationale: "Remove a stuck pod.",
    diff: null,
    requested_at: 1_700_000_000,
  },
  {
    approval_id: "ap-2",
    agent: "terraform",
    tool: "tf_apply",
    args: { working_dir: "/tmp/x" },
    rationale: "Apply infra change.",
    diff: "--- a\n+++ b\n+new line\n",
    requested_at: 1_700_000_100,
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
  window.alert = vi.fn();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("ApprovalsPanel", () => {
  it("renders the empty state when no approvals", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue([]);
    render(<ApprovalsPanel />);
    await waitFor(() => expect(screen.getByText(/no pending approvals/i)).toBeInTheDocument());
  });

  it("renders each pending approval with the agent → tool header + buttons", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue(APPROVALS);
    const { container } = render(<ApprovalsPanel />);
    await waitFor(() => {
      expect(screen.getByText("sysadmin → delete_pod")).toBeInTheDocument();
    });
    expect(screen.getByText("terraform → tf_apply")).toBeInTheDocument();

    const rows = container.querySelectorAll(".approval");
    expect(rows.length).toBe(2);
    expect(rows[0].querySelector(".approve")).not.toBeNull();
    expect(rows[0].querySelector(".reject")).not.toBeNull();
  });

  it("approve flow: prompt → POST /approvals/<id> with approved=true,reason", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue([APPROVALS[0]]);
    const resolveSpy = vi.spyOn(api, "resolveApproval").mockResolvedValue({ resolved: "ok" });
    window.prompt = vi.fn().mockReturnValue("ok");

    render(<ApprovalsPanel />);
    await waitFor(() => screen.getByText("sysadmin → delete_pod"));

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    expect(resolveSpy).toHaveBeenCalledWith("ap-1", {
      approved: true,
      reason: "ok",
    });
  });

  it("reject flow: prompt → POST with approved=false,reason", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue([APPROVALS[0]]);
    const resolveSpy = vi.spyOn(api, "resolveApproval").mockResolvedValue({ resolved: "ok" });
    window.prompt = vi.fn().mockReturnValue("nope");

    render(<ApprovalsPanel />);
    await waitFor(() => screen.getByText("sysadmin → delete_pod"));

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /reject/i }));
    });

    expect(resolveSpy).toHaveBeenCalledWith("ap-1", {
      approved: false,
      reason: "nope",
    });
  });

  it("uses 'no reason given' when prompt returns null", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue([APPROVALS[0]]);
    const resolveSpy = vi.spyOn(api, "resolveApproval").mockResolvedValue({ resolved: "ok" });
    window.prompt = vi.fn().mockReturnValue(null);

    render(<ApprovalsPanel />);
    await waitFor(() => screen.getByText("sysadmin → delete_pod"));

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    expect(resolveSpy).toHaveBeenCalledWith("ap-1", {
      approved: true,
      reason: "no reason given",
    });
  });

  it("refreshes the list after a resolution", async () => {
    const listSpy = vi.spyOn(api, "listApprovals").mockResolvedValue([APPROVALS[0]]);
    vi.spyOn(api, "resolveApproval").mockResolvedValue({ resolved: "ok" });
    window.prompt = vi.fn().mockReturnValue("y");

    render(<ApprovalsPanel />);
    await waitFor(() => screen.getByText("sysadmin → delete_pod"));
    const initialCalls = listSpy.mock.calls.length;

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    });

    // refresh() bumps tick → effect re-runs → listApprovals called again.
    await waitFor(() => {
      expect(listSpy.mock.calls.length).toBeGreaterThan(initialCalls);
    });
  });
});
