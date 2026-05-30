import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuditingPage } from "./AuditingPage";
import { api } from "../api";

// EventSource stub for BusSidebar's useSSE.
class MockEventSource {
  url: string;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  constructor(url: string) { this.url = url; }
  close(): void {}
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
  vi.spyOn(api, "listApprovals").mockResolvedValue([]);
  vi.spyOn(api, "listRollbacks").mockResolvedValue([]);
  vi.spyOn(api, "audit").mockResolvedValue([]);
});
afterEach(() => { vi.unstubAllGlobals(); cleanup(); });

function renderPage(): void {
  render(<MemoryRouter><AuditingPage /></MemoryRouter>);
}

describe("AuditingPage", () => {
  it("renders the page header with the right title and subtitle", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: /auditing/i })).toBeInTheDocument();
    expect(screen.getByText(/live activity · approvals · rollbacks · audit log/i))
      .toBeInTheDocument();
  });

  it("mounts all four section cards (live activity / approvals / rollback / audit log)", async () => {
    renderPage();
    expect(screen.getByTestId("section-live-activity")).toBeInTheDocument();
    expect(screen.getByTestId("section-approvals")).toBeInTheDocument();
    expect(screen.getByTestId("section-rollback")).toBeInTheDocument();
    expect(screen.getByTestId("section-audit-log")).toBeInTheDocument();
    // Each panel's own h2 header is visible (i.e. the components
    // mounted and didn't crash on empty data). Scoped to "heading" so
    // the page subtitle (which mentions all four names) doesn't
    // collide with the panel titles.
    await waitFor(() => expect(
      screen.getByRole("heading", { level: 2, name: /live activity/i }),
    ).toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 2, name: /approval queue/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /rollback queue/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /audit log/i })).toBeInTheDocument();
  });

  it("polls the auditing endpoints on mount", async () => {
    renderPage();
    await waitFor(() => expect(api.listApprovals).toHaveBeenCalled());
    expect(api.listRollbacks).toHaveBeenCalled();
    expect(api.audit).toHaveBeenCalled();
  });
});
