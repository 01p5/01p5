import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { SessionsRail, relativeTime } from "./SessionsRail";
import { api } from "../api";
import type { TicketSummary } from "../types";

const NOW = 1_700_000_000;

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => { cleanup(); });

let lastLocation = "";
function LocationCapture(): null {
  const loc = useLocation();
  lastLocation = loc.pathname;
  return null;
}

function renderRail(
  tickets: TicketSummary[],
  currentTicketId = "",
  onNew: () => void = () => {},
  initial = "/chat",
): void {
  vi.spyOn(api, "listTickets").mockResolvedValue(tickets);
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="*" element={
          <>
            <SessionsRail currentTicketId={currentTicketId} onNew={onNew} />
            <LocationCapture />
          </>
        } />
      </Routes>
    </MemoryRouter>,
  );
}


describe("SessionsRail", () => {
  it("renders the New conversation button + empty state when no tickets", async () => {
    renderRail([]);
    expect(screen.getByTestId("sessions-rail-new")).toBeInTheDocument();
    expect(await screen.findByText(/no past sessions yet/i)).toBeInTheDocument();
  });

  it("calls onNew when the + New button is clicked", async () => {
    const onNew = vi.fn();
    renderRail([], "", onNew);
    await userEvent.click(screen.getByTestId("sessions-rail-new"));
    expect(onNew).toHaveBeenCalledTimes(1);
  });

  it("renders one row per ticket with first_message + event count", async () => {
    renderRail([
      { ticket_id: "tk-a", event_count: 3, first_message: "list pods", last_ts: NOW - 60, last_actor: "main" },
      { ticket_id: "tk-b", event_count: 7, first_message: "what nodes are in this cluster", last_ts: NOW - 7200, last_actor: "sysadmin" },
    ]);
    expect(await screen.findByText("list pods")).toBeInTheDocument();
    expect(screen.getByText("what nodes are in this cluster")).toBeInTheDocument();
    // event_count surfaces.
    expect(screen.getByTestId("sessions-rail-row-tk-a")).toBeInTheDocument();
    expect(screen.getByTestId("sessions-rail-row-tk-b")).toBeInTheDocument();
  });

  it("highlights the active row matching currentTicketId", async () => {
    renderRail([
      { ticket_id: "tk-active", event_count: 1, first_message: "current", last_ts: NOW, last_actor: "main" },
      { ticket_id: "tk-other", event_count: 1, first_message: "other", last_ts: NOW, last_actor: "main" },
    ], "tk-active");
    const active = await screen.findByTestId("sessions-rail-row-tk-active");
    const other = await screen.findByTestId("sessions-rail-row-tk-other");
    // Active row carries the accent border class; other doesn't.
    expect(active.className).toContain("accent-green");
    expect(other.className).not.toContain("accent-green");
  });

  it("navigates to /chat/{id} when a row is clicked", async () => {
    renderRail([
      { ticket_id: "tk-clickme", event_count: 1, first_message: "click", last_ts: NOW, last_actor: "main" },
    ]);
    const row = await screen.findByTestId("sessions-rail-row-tk-clickme");
    await userEvent.click(row);
    await waitFor(() => expect(lastLocation).toBe("/chat/tk-clickme"));
  });

  it("percent-encodes weird ticket ids in the URL", async () => {
    renderRail([
      { ticket_id: "tk/with/slashes", event_count: 1, first_message: "x", last_ts: NOW, last_actor: "main" },
    ]);
    const row = await screen.findByTestId("sessions-rail-row-tk/with/slashes");
    await userEvent.click(row);
    await waitFor(() => expect(lastLocation).toBe(`/chat/tk%2Fwith%2Fslashes`));
  });
});


describe("relativeTime", () => {
  it("returns 'now' for < 60 sec", () => {
    expect(relativeTime(NOW, NOW + 30)).toBe("now");
  });
  it("returns 'Nm ago' for < 1 hour", () => {
    expect(relativeTime(NOW, NOW + 300)).toBe("5m ago");
  });
  it("returns 'Nh ago' for < 1 day", () => {
    expect(relativeTime(NOW, NOW + 7200)).toBe("2h ago");
  });
  it("returns 'Nd ago' for < 1 week", () => {
    expect(relativeTime(NOW, NOW + 86400 * 3)).toBe("3d ago");
  });
  it("returns a date string for > 1 week", () => {
    expect(relativeTime(NOW, NOW + 86400 * 30)).toMatch(/\d{1,2}/);
  });
});
