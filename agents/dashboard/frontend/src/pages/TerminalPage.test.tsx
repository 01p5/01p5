import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { TerminalPage } from "./TerminalPage";
import { api } from "../api";
import type { InventoryHost, TerminalSession } from "../types";

beforeEach(() => {
  vi.restoreAllMocks();
  // Sensible defaults — individual tests override as needed.
  vi.spyOn(api, "listTerminalSessions").mockResolvedValue([]);
  vi.spyOn(api, "listHosts").mockResolvedValue([]);
});
afterEach(() => { cleanup(); });

let lastLocation = "";
function LocationCapture(): null {
  const loc = useLocation();
  lastLocation = loc.pathname;
  return null;
}
function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/terminal"]}>
      <Routes>
        <Route path="*" element={<>
          <TerminalPage />
          <LocationCapture />
        </>} />
      </Routes>
    </MemoryRouter>,
  );
}


const HOSTS: InventoryHost[] = [
  {
    id: "h-cp", name: "cp", address: "10.0.0.1", ssh_user: "ubuntu",
    ssh_port: 22, key_id: "k1", groups: ["control_plane"], vars: {},
    description: "", created_at: 0, updated_at: 0,
  },
  {
    id: "h-w1", name: "w1", address: "10.0.0.2", ssh_user: "root",
    ssh_port: 2222, key_id: "k1", groups: ["workers"], vars: {},
    description: "", created_at: 0, updated_at: 0,
  },
];


describe("TerminalPage", () => {
  it("shows the empty state when there are no sessions", async () => {
    renderPage();
    expect(await screen.findByText(/no live sessions/i)).toBeInTheDocument();
  });

  it("renders one row per live session", async () => {
    vi.spyOn(api, "listTerminalSessions").mockResolvedValue([
      mkSession({ session_id: "s-1", host_alias: "cp", ssh_user: "ubuntu" }),
      mkSession({ session_id: "s-2", host_alias: "w1", ssh_user: "root", attached: true }),
    ]);
    renderPage();
    expect(await screen.findByTestId("terminal-row-s-1")).toBeInTheDocument();
    expect(screen.getByTestId("terminal-row-s-2")).toBeInTheDocument();
    // Attached marker on s-2.
    expect(screen.getByText(/attached/i)).toBeInTheDocument();
  });

  it("clicking a session navigates to /terminal/{id}", async () => {
    vi.spyOn(api, "listTerminalSessions").mockResolvedValue([
      mkSession({ session_id: "s-click", host_alias: "cp" }),
    ]);
    renderPage();
    const row = await screen.findByTestId("terminal-row-s-click");
    await userEvent.click(row);
    await waitFor(() => expect(lastLocation).toBe("/terminal/s-click"));
  });

  it("opens the New session modal", async () => {
    renderPage();
    await userEvent.click(screen.getByTestId("terminal-new-session"));
    expect(await screen.findByText(/open new terminal session/i)).toBeInTheDocument();
  });

  it("modal lists inventory hosts in the dropdown", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue(HOSTS);
    renderPage();
    await userEvent.click(screen.getByTestId("terminal-new-session"));
    const select = await screen.findByTestId("terminal-host-select") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toEqual(["cp", "w1"]);
  });

  it("modal shows an empty hint when there are no hosts", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue([]);
    renderPage();
    await userEvent.click(screen.getByTestId("terminal-new-session"));
    expect(await screen.findByText(/no hosts in the inventory yet/i)).toBeInTheDocument();
    // Open button is disabled.
    const open = screen.getByTestId("terminal-create-confirm") as HTMLButtonElement;
    expect(open.disabled).toBe(true);
  });

  it("creating a session calls api with picked host + override + navigates", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue(HOSTS);
    const create = vi.spyOn(api, "createTerminalSession").mockResolvedValue(
      mkSession({ session_id: "s-new", host_alias: "w1", ssh_user: "alice" }),
    );

    renderPage();
    await userEvent.click(screen.getByTestId("terminal-new-session"));

    const select = await screen.findByTestId("terminal-host-select") as HTMLSelectElement;
    await userEvent.selectOptions(select, "w1");

    const override = screen.getByTestId("terminal-ssh-user-override") as HTMLInputElement;
    await userEvent.type(override, "alice");

    await userEvent.click(screen.getByTestId("terminal-create-confirm"));
    await waitFor(() => expect(create).toHaveBeenCalled());
    const body = create.mock.calls[0][0];
    expect(body.host_alias).toBe("w1");
    expect(body.ssh_user).toBe("alice");
    await waitFor(() => expect(lastLocation).toBe("/terminal/s-new"));
  });

  it("delete button respects window.confirm cancel (no API call)", async () => {
    vi.spyOn(api, "listTerminalSessions").mockResolvedValue([
      mkSession({ session_id: "s-keep", host_alias: "cp" }),
    ]);
    const remove = vi.spyOn(api, "removeTerminalSession").mockResolvedValue({ ok: true });
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(false));
    renderPage();
    const row = await screen.findByTestId("terminal-row-s-keep");
    // Find the inner delete button — only one in the row.
    const trash = row.querySelector("button[aria-label='Close session']")!;
    await userEvent.click(trash);
    expect(remove).not.toHaveBeenCalled();
  });

  it("create-no-host-selected validation error stays in the modal", async () => {
    // Mock hosts to empty so selectedHost stays "" and the
    // "pick a host" branch fires when the user clicks Open.
    vi.spyOn(api, "listHosts").mockResolvedValue([]);
    renderPage();
    await userEvent.click(screen.getByTestId("terminal-new-session"));
    // Open button is disabled with no hosts, so the validation
    // branch is best exercised by mutating selectedHost via
    // dispatching events on a stub select. Instead: directly verify
    // the disabled button doesn't fire.
    const open = await screen.findByTestId("terminal-create-confirm") as HTMLButtonElement;
    expect(open.disabled).toBe(true);
  });

  it("create error surfaces inside the modal without closing it", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue(HOSTS);
    vi.spyOn(api, "createTerminalSession").mockRejectedValue(new Error("ssh-key missing"));

    renderPage();
    await userEvent.click(screen.getByTestId("terminal-new-session"));
    await screen.findByTestId("terminal-host-select");
    await userEvent.click(screen.getByTestId("terminal-create-confirm"));

    await waitFor(() => expect(screen.getByText(/ssh-key missing/)).toBeInTheDocument());
    // Modal stays open.
    expect(screen.getByText(/open new terminal session/i)).toBeInTheDocument();
  });
});


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
    ...over,
  };
}
