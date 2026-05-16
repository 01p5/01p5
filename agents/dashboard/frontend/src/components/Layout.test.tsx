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
          <Route path="kubernetes" element={<div>k8s-content</div>} />
          <Route path="terraform" element={<div>tf-content</div>} />
          <Route path="ansible" element={<div>ansible-content</div>} />
          <Route path="programmer" element={<div>prog-content</div>} />
          <Route path="mcp" element={<div>mcp-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout", () => {
  it("renders all 6 tabs and the Olympus brand", () => {
    renderLayout();
    expect(screen.getByText(/^olympus$/i)).toBeInTheDocument();
    ["Chat", "Kubernetes", "Terraform", "Ansible", "Programmer", "MCP"].forEach((label) => {
      expect(screen.getByRole("link", { name: new RegExp(label, "i") })).toBeInTheDocument();
    });
  });

  it("renders the Outlet content for the active route", async () => {
    renderLayout("/chat");
    await waitFor(() => expect(screen.getByText("chat-content")).toBeInTheDocument());
  });

  it("marks the matching NavLink as active (NavLink applies an aria-current)", () => {
    renderLayout("/terraform");
    // react-router NavLink sets aria-current="page" on the active link.
    const active = screen.getByRole("link", { name: /terraform/i });
    expect(active).toHaveAttribute("aria-current", "page");
    // Other tabs are NOT active.
    expect(screen.getByRole("link", { name: /chat/i })).not.toHaveAttribute("aria-current");
  });

  it("clicking a tab changes the active state", async () => {
    renderLayout("/chat");
    // Initially Chat is active.
    expect(screen.getByRole("link", { name: /chat/i })).toHaveAttribute("aria-current", "page");
    await userEvent.click(screen.getByRole("link", { name: /kubernetes/i }));
    await waitFor(() => {
      expect(screen.getByRole("link", { name: /kubernetes/i })).toHaveAttribute("aria-current", "page");
    });
    expect(screen.getByRole("link", { name: /chat/i })).not.toHaveAttribute("aria-current");
    // And the Outlet swapped.
    expect(screen.getByText("k8s-content")).toBeInTheDocument();
  });

  it("right sidebar surfaces the three intelligence-layer panels", async () => {
    renderLayout("/chat");
    // Approvals + Rollback + Audit panels are all mounted in the right column.
    await waitFor(() => expect(screen.getByText(/approval queue/i)).toBeInTheDocument());
    expect(screen.getByText(/rollback queue/i)).toBeInTheDocument();
    expect(screen.getByText(/audit log/i)).toBeInTheDocument();
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
