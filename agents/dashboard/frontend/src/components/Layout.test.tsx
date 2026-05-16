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
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout", () => {
  it("renders all 5 tabs and the Olympus brand", () => {
    renderLayout();
    expect(screen.getByText(/^olympus$/i)).toBeInTheDocument();
    ["Chat", "Kubernetes", "Terraform", "Ansible", "Programmer"].forEach((label) => {
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
});
