import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SessionsPage } from "./SessionsPage";
import { api } from "../api";

afterEach(() => { vi.restoreAllMocks(); cleanup(); });

function renderPage() {
  return render(<MemoryRouter><SessionsPage /></MemoryRouter>);
}

describe("SessionsPage", () => {
  it("renders past tickets with preview + counts", async () => {
    vi.spyOn(api, "listTickets").mockResolvedValue([
      { ticket_id: "tk-abc123def456", event_count: 4, first_message: "list pods", last_ts: 1700000000, last_actor: "main" },
    ]);
    renderPage();
    expect(await screen.findByText("list pods")).toBeInTheDocument();
    expect(screen.getByText(/4 events/)).toBeInTheDocument();
    expect(screen.getByText(/last: main/)).toBeInTheDocument();
  });

  it("shows an empty state when there are no sessions", async () => {
    vi.spyOn(api, "listTickets").mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/no sessions yet/i)).toBeInTheDocument();
  });

  it("shows an error when listing fails", async () => {
    vi.spyOn(api, "listTickets").mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText(/failed to load: boom/i)).toBeInTheDocument();
  });
});
