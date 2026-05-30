import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { Layout } from "./Layout";
import { api } from "../api";

// EventSource stub for the BusSidebar that the Layout includes.
class MockEventSource {
  url: string;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  constructor(url: string) {
    this.url = url;
  }
  close(): void {}
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
  vi.spyOn(api, "health").mockResolvedValue({ ok: true });
  vi.spyOn(api, "listApprovals").mockResolvedValue([]);
  vi.spyOn(api, "audit").mockResolvedValue([]);
  vi.spyOn(api, "listRollbacks").mockResolvedValue([]);
  vi.spyOn(api, "telemetry").mockResolvedValue({
    totals: { tasks: 0, settled: 0, usd: 0, input_tokens: 0, output_tokens: 0, wall_seconds: 0 },
    by_agent: {},
    by_status: {},
    recent: [],
  });
  vi.spyOn(api, "me").mockResolvedValue({
    authenticated: true, email: "test@stanford.edu",
    auth: { bypass: true, google_oauth: false, email_otp: false, allowed_domains: ["stanford.edu"], cookie_secure: false },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function renderLayout(initial = "/chat"): void {
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route path="chat" element={<div>chat-content</div>} />
          <Route path="sessions" element={<div>sessions-content</div>} />
          <Route path="auditing" element={<div>auditing-content</div>} />
          <Route path="capabilities/*" element={<div>caps-content</div>} />
          <Route path="kubernetes" element={<div>k8s-content</div>} />
          <Route path="terraform" element={<div>tf-content</div>} />
          <Route path="ansible" element={<div>ansible-content</div>} />
          <Route path="hosts" element={<div>hosts-content</div>} />
          <Route path="programmer" element={<div>prog-content</div>} />
          <Route path="mcp" element={<div>mcp-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout", () => {
  it("renders every tab in the nav and the Olympus brand", () => {
    renderLayout();
    expect(screen.getByText(/^olympus$/i)).toBeInTheDocument();
    // CHAT.1 dropped Sessions; NAV.1 collapsed K8s/TF/Ansible/Programmer
    // into Capabilities. Topnav is intentionally compact now.
    ["Chat", "Auditing", "Capabilities", "Hosts", "MCP"].forEach((label) => {
      expect(screen.getByRole("link", { name: new RegExp(label, "i") })).toBeInTheDocument();
    });
    expect(screen.queryByRole("link", { name: /^sessions$/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /^kubernetes$/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /^terraform$/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /^ansible$/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /^programmer$/i })).toBeNull();
  });

  it("renders the Outlet content for the active route", async () => {
    renderLayout("/chat");
    await waitFor(() => expect(screen.getByText("chat-content")).toBeInTheDocument());
  });

  it("marks the matching NavLink as active (NavLink applies an aria-current)", () => {
    renderLayout("/auditing");
    const active = screen.getByRole("link", { name: /auditing/i });
    expect(active).toHaveAttribute("aria-current", "page");
    // Other tabs are NOT active.
    expect(screen.getByRole("link", { name: /chat/i })).not.toHaveAttribute("aria-current");
  });

  it("clicking a tab changes the active state", async () => {
    renderLayout("/chat");
    // Initially Chat is active.
    expect(screen.getByRole("link", { name: /chat/i })).toHaveAttribute("aria-current", "page");
    await userEvent.click(screen.getByRole("link", { name: /capabilities/i }));
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /capabilities/i })).toHaveAttribute("aria-current", "page");
    });
    expect(screen.getByRole("link", { name: /chat/i })).not.toHaveAttribute("aria-current");
    // And the Outlet swapped.
    expect(screen.getByText("caps-content")).toBeInTheDocument();
  });

  it("does NOT mount the auditing panels in the Layout (they moved to /auditing)", () => {
    renderLayout("/chat");
    // Approval queue / Rollback queue / Audit log / Live Activity used to
    // ride along in left + right side panels; AUD.2 moves them to /auditing
    // and exposes them only via the new Auditing tab.
    expect(screen.queryByText(/approval queue/i)).toBeNull();
    expect(screen.queryByText(/rollback queue/i)).toBeNull();
    expect(screen.queryByText(/audit log/i)).toBeNull();
    expect(screen.queryByText(/live activity/i)).toBeNull();
    // But the Auditing tab IS there, linking to /auditing.
    const tab = screen.getByRole("link", { name: /auditing/i });
    expect(tab).toHaveAttribute("href", "/auditing");
  });

  it("telemetry footer mounts and stays hidden until tasks > 0", async () => {
    // Empty telemetry (default mock) → no footer visible.
    renderLayout("/chat");
    await waitFor(() => expect(api.telemetry).toHaveBeenCalled());
    expect(document.getElementById("telemetry-footer")).toBeNull();
  });

  it("telemetry footer renders once tasks > 0", async () => {
    vi.spyOn(api, "telemetry").mockResolvedValue({
      totals: { tasks: 2, settled: 2, usd: 0.005, input_tokens: 400, output_tokens: 100, wall_seconds: 6 },
      by_agent: { sysadmin: { tasks: 2, usd: 0.005, input_tokens: 400, output_tokens: 100, wall_seconds: 6 } },
      by_status: { success: 2 },
      recent: [],
    });
    renderLayout("/chat");
    await waitFor(() =>
      expect(document.getElementById("telemetry-footer")).not.toBeNull(),
    );
    expect(screen.getByText(/2\/2 tasks/)).toBeInTheDocument();
  });
});
