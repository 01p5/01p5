import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { TerminalSessionsRail } from "./TerminalSessionsRail";
import { api } from "../api";
import type { TerminalSession } from "../types";


let lastLocation = "";
function LocationCapture(): null {
  const loc = useLocation();
  lastLocation = loc.pathname;
  return null;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "listTerminalSessions").mockResolvedValue([]);
  vi.spyOn(api, "listHosts").mockResolvedValue([]);
});
afterEach(() => { cleanup(); });

function renderRail(sessions: TerminalSession[], currentId = ""): void {
  vi.spyOn(api, "listTerminalSessions").mockResolvedValue(sessions);
  render(
    <MemoryRouter initialEntries={[`/terminal/${currentId || "x"}`]}>
      <Routes>
        <Route path="*" element={<>
          <TerminalSessionsRail currentSessionId={currentId} />
          <LocationCapture />
        </>} />
      </Routes>
    </MemoryRouter>,
  );
}


function mkSession(over: Partial<TerminalSession>): TerminalSession {
  return {
    session_id: "s-default",
    host_alias: "cp",
    ssh_user: "ubuntu",
    address: "10.0.0.1",
    created_at: 1_700_000_000,
    attached: false,
    last_active_at: 1_700_000_000,
    alive: true,
    kind: null,
    ...over,
  };
}


describe("TerminalSessionsRail", () => {
  it("renders the New session button + empty state when no sessions", async () => {
    renderRail([]);
    expect(screen.getByTestId("terminal-rail-new")).toBeInTheDocument();
    expect(await screen.findByText(/no live sessions/i)).toBeInTheDocument();
  });

  it("renders one row per live session", async () => {
    renderRail([
      mkSession({ session_id: "s-a", host_alias: "cp" }),
      mkSession({ session_id: "s-b", host_alias: "w1", ssh_user: "root" }),
    ]);
    expect(await screen.findByTestId("terminal-rail-row-s-a")).toBeInTheDocument();
    expect(screen.getByTestId("terminal-rail-row-s-b")).toBeInTheDocument();
  });

  it("highlights the active row matching currentSessionId", async () => {
    renderRail([
      mkSession({ session_id: "s-active", host_alias: "cp" }),
      mkSession({ session_id: "s-other", host_alias: "w1" }),
    ], "s-active");
    const active = await screen.findByTestId("terminal-rail-row-s-active");
    const other = await screen.findByTestId("terminal-rail-row-s-other");
    expect(active.className).toContain("accent-green");
    expect(other.className).not.toContain("accent-green");
  });

  it("navigates to /terminal/{id} when a row is clicked", async () => {
    renderRail([mkSession({ session_id: "s-clickme", host_alias: "cp" })]);
    const row = await screen.findByTestId("terminal-rail-row-s-clickme");
    await userEvent.click(row);
    await waitFor(() => expect(lastLocation).toBe("/terminal/s-clickme"));
  });

  it("clicking + New opens the modal", async () => {
    renderRail([]);
    await userEvent.click(screen.getByTestId("terminal-rail-new"));
    expect(await screen.findByText(/open new terminal session/i)).toBeInTheDocument();
  });

  it("modal's Olympus CLI quick-start posts kind=olympus-tui + navigates (TERM.9b)", async () => {
    const create = vi.spyOn(api, "createTerminalSession").mockResolvedValue(
      mkSession({ session_id: "s-cli", host_alias: "olympus-tui",
                  address: "(local)", ssh_user: "", kind: "olympus-tui" }),
    );
    renderRail([]);
    await userEvent.click(screen.getByTestId("terminal-rail-new"));
    await userEvent.click(await screen.findByTestId("terminal-quickstart-olympus-cli"));
    await waitFor(() => expect(create).toHaveBeenCalled());
    // Body shape: just {kind} — no host_alias required.
    const body = create.mock.calls[0][0];
    expect(body).toEqual({ kind: "olympus-tui" });
    await waitFor(() => expect(lastLocation).toBe("/terminal/s-cli"));
  });

  it("local-CLI session row shows the kind label instead of user@host", async () => {
    renderRail([
      mkSession({ session_id: "s-cli", host_alias: "olympus-tui",
                  ssh_user: "", address: "(local)", kind: "olympus-tui" }),
    ]);
    const row = await screen.findByTestId("terminal-rail-row-s-cli");
    // Doesn't render "@" + address for local sessions.
    expect(row.textContent).not.toMatch(/@\(local\)/);
    expect(row.textContent).toMatch(/local/i);
  });
});
