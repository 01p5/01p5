import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryChips } from "./MemoryChips";
import { api } from "../api";
import type { MemoryEntry } from "../types";

const ENTRY = (over: Partial<MemoryEntry> = {}): MemoryEntry => ({
  task_id: "T-prior",
  agent: "sysadmin",
  natural_language: "delete pod nginx in default namespace",
  summary: "approved + deleted pod nginx",
  status: "success",
  ts: 1_700_000_000,
  metadata: {},
  ...over,
});

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("MemoryChips", () => {
  it("renders nothing when no prior entries", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([]);
    const { container } = render(
      <MemoryChips taskId="T-now" query="list pods" agent="sysadmin" />,
    );
    await waitFor(() => expect(api.listMemory).toHaveBeenCalled());
    expect(container.querySelector(".memory-chips")).toBeNull();
  });

  it("renders one chip per prior entry with truncated preview", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([
      ENTRY({ task_id: "T1" }),
      ENTRY({ task_id: "T2", natural_language: "x".repeat(80) }),
    ]);
    render(<MemoryChips taskId="T-now" query="list pods" agent="sysadmin" />);
    await waitFor(() => expect(screen.getAllByText(/delete pod nginx/i).length).toBeGreaterThan(0));
    expect(screen.getByText(/seen before/i)).toBeInTheDocument();
    // Truncation: 60 chars + "…"
    const long = screen.getByText(/^x{60}…$/);
    expect(long).toBeInTheDocument();
  });

  it("queries /memory with q + agent + k=3 by default", async () => {
    const spy = vi.spyOn(api, "listMemory").mockResolvedValue([]);
    render(<MemoryChips taskId="T-now" query="delete pod web" agent="sysadmin" />);
    await waitFor(() => expect(spy).toHaveBeenCalledWith({
      q: "delete pod web",
      agent: "sysadmin",
      k: 3,
    }));
  });

  it("filters out the self-entry so a freshly-written turn doesn't match itself", async () => {
    vi.spyOn(api, "listMemory").mockResolvedValue([
      ENTRY({ task_id: "T-now", natural_language: "delete pod self" }),
      ENTRY({ task_id: "T-other", natural_language: "delete pod other" }),
    ]);
    render(<MemoryChips taskId="T-now" query="delete pod" agent="sysadmin" />);
    await waitFor(() => expect(screen.getByText(/delete pod other/i)).toBeInTheDocument());
    expect(screen.queryByText(/delete pod self/i)).toBeNull();
  });

  it("good-feedback entries get the green tone class", async () => {
    const { container } = render(
      <MemoryChips
        taskId="T-now"
        query="x"
        agent="sysadmin"
        entries={[ENTRY({ task_id: "T1", metadata: { feedback: "good" } })]}
      />,
    );
    const chip = container.querySelector(".memory-chip");
    expect(chip?.className).toMatch(/text-accent-green/);
    expect(chip?.getAttribute("data-feedback")).toBe("good");
  });

  it("controlled mode (entries prop) skips the network call", async () => {
    const spy = vi.spyOn(api, "listMemory");
    render(
      <MemoryChips
        taskId="T-now"
        query="x"
        agent="sysadmin"
        entries={[ENTRY({ task_id: "T1" })]}
      />,
    );
    // No network call when entries are provided.
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByText(/delete pod nginx/i)).toBeInTheDocument();
  });

  it("renders an error chip if /memory fails", async () => {
    vi.spyOn(api, "listMemory").mockRejectedValue(new Error("503 Service Unavailable"));
    render(<MemoryChips taskId="T-now" query="x" agent="sysadmin" />);
    await waitFor(() =>
      expect(screen.getByText(/memory unavailable/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/503/)).toBeInTheDocument();
  });

  it("renders correction text in the chip tooltip", async () => {
    const { container } = render(
      <MemoryChips
        taskId="T-now"
        query="x"
        agent="sysadmin"
        entries={[
          ENTRY({
            task_id: "T1",
            metadata: {
              feedback: "good",
              correction: "use --namespace=staging next time",
            },
          }),
        ]}
      />,
    );
    const chip = container.querySelector(".memory-chip");
    expect(chip?.getAttribute("title")).toContain("correction: use --namespace=staging");
  });
});
