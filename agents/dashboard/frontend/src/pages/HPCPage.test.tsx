import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HPCPage, extractText } from "./HPCPage";
import { api } from "../api";


vi.mock("../api", () => ({
  api: {
    listMcpServers: vi.fn(),
    invokeTool: vi.fn(),
  },
}));


function renderPage() {
  return render(
    <MemoryRouter>
      <HPCPage />
    </MemoryRouter>,
  );
}


describe("HPCPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows nudge when neither MCP server is connected", async () => {
    (api.listMcpServers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("hpc-not-connected")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("hpc-slurm-section")).not.toBeInTheDocument();
    expect(screen.queryByTestId("hpc-gpu-section")).not.toBeInTheDocument();
  });

  it("renders both sections + calls invokeTool when both servers are connected", async () => {
    (api.listMcpServers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { name: "gpu-mcp", status: "connected", tool_count: 6, tools: [], destructive: [], target_agent: "hpc", command: "gpu-mcp", error: null },
      { name: "slurm-mcp", status: "connected", tool_count: 11, tools: [], destructive: [], target_agent: "hpc", command: "slurm-mcp", error: null },
    ]);
    (api.invokeTool as ReturnType<typeof vi.fn>).mockResolvedValue({
      result: { content: [{ type: "text", text: "ok" }] },
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("hpc-slurm-section")).toBeInTheDocument();
      expect(screen.getByTestId("hpc-gpu-section")).toBeInTheDocument();
    });

    // Each section's tools should have been invoked exactly once.
    await waitFor(() => {
      const calls = (api.invokeTool as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[1]);
      expect(calls).toContain("slurm-mcp_nodes_list");
      expect(calls).toContain("slurm-mcp_jobs_list");
      expect(calls).toContain("slurm-mcp_partitions_list");
      expect(calls).toContain("slurm-mcp_diagnostics_show");
      expect(calls).toContain("gpu-mcp_nodes_list");
      expect(calls).toContain("gpu-mcp_fleet_summary");
      expect(calls).toContain("gpu-mcp_drain_advisor");
      expect(calls).toContain("gpu-mcp_node_status");
    });
  });

  it("renders partial nudge when only one server is connected", async () => {
    (api.listMcpServers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { name: "gpu-mcp", status: "connected", tool_count: 6, tools: [], destructive: [], target_agent: "hpc", command: "gpu-mcp", error: null },
    ]);
    (api.invokeTool as ReturnType<typeof vi.fn>).mockResolvedValue({ result: "ok" });

    renderPage();

    await waitFor(() => {
      // nudge appears (because slurm-mcp missing)
      expect(screen.getByTestId("hpc-not-connected")).toBeInTheDocument();
      // gpu section still renders (best-effort partial view)
      expect(screen.getByTestId("hpc-gpu-section")).toBeInTheDocument();
      // slurm section does not
      expect(screen.queryByTestId("hpc-slurm-section")).not.toBeInTheDocument();
    });
  });
});


describe("extractText", () => {
  it("returns empty string for null/undefined", () => {
    expect(extractText(null)).toBe("");
    expect(extractText(undefined)).toBe("");
  });

  it("returns string as-is", () => {
    expect(extractText("hello")).toBe("hello");
  });

  it("joins MCP content array text parts", () => {
    expect(
      extractText({
        content: [
          { type: "text", text: "line 1" },
          { type: "text", text: "line 2" },
        ],
      }),
    ).toBe("line 1\nline 2");
  });

  it("falls back to JSON.stringify for unknown shapes", () => {
    expect(extractText({ foo: 1 })).toContain("\"foo\": 1");
  });

  it("reads .text directly if present", () => {
    expect(extractText({ text: "direct" })).toBe("direct");
  });
});
