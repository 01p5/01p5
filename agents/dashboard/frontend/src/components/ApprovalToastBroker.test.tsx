import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { toast } from "sonner";
import { ApprovalToastBroker, currentChatTicketId } from "./ApprovalToastBroker";
import { api } from "../api";
import type { PendingApproval } from "../types";

function mkApproval(over: Partial<PendingApproval>): PendingApproval {
  return {
    approval_id: "ap-default",
    agent: "sysadmin",
    tool: "delete_pod",
    args: {},
    rationale: "default rationale",
    diff: null,
    requested_at: 1,
    ticket_id: null,
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(toast, "custom").mockReturnValue("toast-1");
  vi.spyOn(toast, "dismiss").mockImplementation(() => "");
});
afterEach(() => { cleanup(); });

function renderBroker(initial = "/chat"): void {
  render(
    <MemoryRouter initialEntries={[initial]}>
      <ApprovalToastBroker />
    </MemoryRouter>,
  );
}

describe("ApprovalToastBroker", () => {
  it("fires toast.custom for a new approval when the user is NOT on the matching chat", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue([
      mkApproval({ approval_id: "ap-1", ticket_id: "tk-789" }),
    ]);
    renderBroker("/kubernetes");
    await waitFor(() => expect(toast.custom).toHaveBeenCalled());
  });

  it("does NOT fire toast when the user is on /chat/{matching ticket id}", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue([
      mkApproval({ approval_id: "ap-1", ticket_id: "tk-789" }),
    ]);
    renderBroker("/chat/tk-789");
    // Give the polling effect a chance to run.
    await waitFor(() => expect(api.listApprovals).toHaveBeenCalled());
    expect(toast.custom).not.toHaveBeenCalled();
  });

  it("fires toast for approval from a DIFFERENT ticket even while user is on a chat", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue([
      mkApproval({ approval_id: "ap-1", ticket_id: "tk-other" }),
    ]);
    renderBroker("/chat/tk-mine");
    await waitFor(() => expect(toast.custom).toHaveBeenCalled());
  });

  it("fires for approvals with NO ticket_id (standalone /tasks runs) regardless of route", async () => {
    vi.spyOn(api, "listApprovals").mockResolvedValue([
      mkApproval({ approval_id: "ap-1", ticket_id: null }),
    ]);
    renderBroker("/chat/tk-mine");
    await waitFor(() => expect(toast.custom).toHaveBeenCalled());
  });

  it("dismisses orphan toasts when an approval is resolved out-of-band", async () => {
    const spy = vi.spyOn(api, "listApprovals")
      .mockResolvedValueOnce([mkApproval({ approval_id: "ap-1", ticket_id: "tk-1" })])
      .mockResolvedValue([]);  // gone on subsequent polls
    renderBroker("/kubernetes");
    await waitFor(() => expect(toast.custom).toHaveBeenCalledTimes(1));
    // Force a re-poll by waiting for the next listApprovals call.
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2), { timeout: 3000 });
    await waitFor(() => expect(toast.dismiss).toHaveBeenCalled());
  });
});

describe("currentChatTicketId", () => {
  it("returns the ticket id from /chat/{id}", () => {
    expect(currentChatTicketId("/chat/tk-123")).toBe("tk-123");
  });

  it("decodes a percent-encoded ticket id", () => {
    expect(currentChatTicketId("/chat/tk%2Fweird")).toBe("tk/weird");
  });

  it("returns null on /chat (no id)", () => {
    expect(currentChatTicketId("/chat")).toBe(null);
  });

  it("returns null on a non-chat route", () => {
    expect(currentChatTicketId("/kubernetes")).toBe(null);
    expect(currentChatTicketId("/auditing")).toBe(null);
    expect(currentChatTicketId("/")).toBe(null);
  });

  it("returns null for /chat/ with empty trailing segment", () => {
    expect(currentChatTicketId("/chat/")).toBe(null);
  });

  it("strips a sub-path off /chat/{id}/whatever", () => {
    expect(currentChatTicketId("/chat/tk-1/extra")).toBe("tk-1");
  });
});
