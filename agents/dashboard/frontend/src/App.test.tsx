import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import { api } from "./api";

// Smoke test for App.tsx routing. The Layout pulls in BusSidebar /
// Approvals / Audit / Rollback / Telemetry, each of which fires an
// API call on mount — we stub everything to keep the test hermetic.

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "health").mockResolvedValue({ ok: true });
  vi.spyOn(api, "listApprovals").mockResolvedValue([]);
  vi.spyOn(api, "audit").mockResolvedValue([]);
  vi.spyOn(api, "listRollbacks").mockResolvedValue([]);
  vi.spyOn(api, "telemetry").mockResolvedValue({
    totals: { tasks: 0, settled: 0, usd: 0, input_tokens: 0, output_tokens: 0, wall_seconds: 0 },
    by_agent: {}, by_status: {}, recent: [],
  });
  vi.spyOn(api, "terraformStacks").mockResolvedValue([]);
  vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue([]);
  vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
  vi.spyOn(api, "listTools").mockResolvedValue([]);
  vi.spyOn(api, "listTasks").mockResolvedValue([]);
  vi.spyOn(api, "invokeTool").mockResolvedValue({ task_id: "t", agent: "x", tool: "y", result: "" });
  // EventSource isn't in happy-dom; pages that use it won't crash because
  // useSSE handles `typeof EventSource === "undefined"` defensively, but
  // stubbing it keeps things quiet.
  (globalThis as { EventSource?: unknown }).EventSource = class {
    addEventListener(): void {}
    close(): void {}
  };
});
afterEach(() => { cleanup(); });

const renderAt = (path: string): void => {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
};

describe("App routing", () => {
  it("/ redirects to /chat", async () => {
    renderAt("/");
    // ChatPage renders an empty-state hint when there are no tasks.
    await waitFor(() => expect(document.querySelectorAll("nav a, header a").length).toBeGreaterThan(0));
    // The Chat tab is active after the redirect.
    const chatLink = screen.getByRole("link", { name: /chat/i });
    expect(chatLink).toBeInTheDocument();
  });

  it("/terraform renders the Terraform page", async () => {
    renderAt("/terraform");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: /terraform/i })).toBeInTheDocument());
  });

  it("/ansible renders the Ansible page", async () => {
    renderAt("/ansible");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: /ansible/i })).toBeInTheDocument());
  });

  it("/kubernetes renders the Kubernetes page", async () => {
    renderAt("/kubernetes");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: /kubernetes/i })).toBeInTheDocument());
  });

  it("/mcp renders the MCP page", async () => {
    renderAt("/mcp");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: /mcp servers/i })).toBeInTheDocument());
  });

  it("/programmer renders the Programmer page", async () => {
    renderAt("/programmer");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: /programmer/i })).toBeInTheDocument());
  });

  it("unknown path falls back to chat (catch-all redirect)", async () => {
    renderAt("/this-does-not-exist");
    // After redirect the top nav is present, with Chat highlighted.
    await waitFor(() => expect(screen.getByRole("link", { name: /chat/i })).toBeInTheDocument());
  });
});
