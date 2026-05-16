import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MCPPage } from "./MCPPage";
import { api } from "../api";
import type { MCPServerSummary, MCPServerCatalog } from "../types";

const SERVER = (over: Partial<MCPServerSummary> = {}): MCPServerSummary => ({
  name: "filesystem",
  target_agent: "programmer",
  command: "npx fs-server /tmp",
  tool_count: 2,
  tools: ["read", "write_file"],
  destructive: ["filesystem_write_file"],
  status: "connected",
  error: null,
  ...over,
});

const CATALOG: MCPServerCatalog = {
  name: "filesystem",
  tools: [
    {
      name: "read",
      description: "Read a file by path",
      inputSchema: { type: "object", properties: { path: { type: "string" } } },
    },
    {
      name: "write_file",
      description: "Write content to a path",
      inputSchema: { type: "object" },
    },
  ],
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("MCPPage", () => {
  it("renders the empty state when no servers are wired", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    render(<MCPPage />);
    await waitFor(() =>
      expect(screen.getByText(/No MCP servers wired/i)).toBeInTheDocument(),
    );
  });

  it("renders one card per server with name + target + tool count", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ name: "filesystem" }),
      SERVER({ name: "github", target_agent: "sysadmin", tool_count: 4 }),
    ]);
    const { container } = render(<MCPPage />);
    await waitFor(() =>
      expect(container.querySelectorAll(".mcp-server-card").length).toBe(2),
    );
    expect(screen.getByText("filesystem")).toBeInTheDocument();
    expect(screen.getByText("github")).toBeInTheDocument();
    expect(screen.getByText(/→ programmer/)).toBeInTheDocument();
    expect(screen.getByText(/→ sysadmin/)).toBeInTheDocument();
  });

  it("error-status server gets the red tone and surfaces the error string", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ name: "flaky", status: "error", error: "ECONNREFUSED" }),
    ]);
    const { container } = render(<MCPPage />);
    await waitFor(() =>
      expect(container.querySelector('[data-server-status="error"]')).not.toBeNull(),
    );
    expect(screen.getByText(/ECONNREFUSED/)).toBeInTheDocument();
  });

  it("clicking 'show tools' fetches the catalog and renders each tool", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([SERVER()]);
    const catalogSpy = vi.spyOn(api, "getMcpServerTools").mockResolvedValue(CATALOG);
    const { container } = render(<MCPPage />);
    await waitFor(() => screen.getByText(/show 2 tools/i));

    await act(async () => {
      await userEvent.click(screen.getByText(/show 2 tools/i));
    });

    await waitFor(() => expect(catalogSpy).toHaveBeenCalledWith("filesystem"));
    // Both tools render with the server-name prefix.
    expect(screen.getByText("filesystem_read")).toBeInTheDocument();
    expect(screen.getByText("filesystem_write_file")).toBeInTheDocument();
    // Descriptions show.
    expect(screen.getByText(/Read a file by path/)).toBeInTheDocument();
    // The destructive tool gets the destructive tag.
    const destrTool = container.querySelector('[data-tool-name="write_file"]');
    expect(destrTool?.getAttribute("data-destructive")).toBe("1");
    const readTool = container.querySelector('[data-tool-name="read"]');
    expect(readTool?.getAttribute("data-destructive")).toBe("0");
  });

  it("clicking the show-tools button again collapses the list (one fetch only)", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([SERVER()]);
    const catalogSpy = vi.spyOn(api, "getMcpServerTools").mockResolvedValue(CATALOG);
    render(<MCPPage />);
    await waitFor(() => screen.getByText(/show 2 tools/i));

    const toggleButton = screen.getByText(/show 2 tools/i);
    await act(async () => {
      await userEvent.click(toggleButton);
    });
    await waitFor(() => screen.getByText("filesystem_read"));
    await act(async () => {
      await userEvent.click(screen.getByText(/hide 2 tools/i));
    });
    expect(screen.queryByText("filesystem_read")).toBeNull();
    expect(catalogSpy).toHaveBeenCalledTimes(1);
  });

  it("shows a load error when the catalog fetch fails", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([SERVER()]);
    vi.spyOn(api, "getMcpServerTools").mockRejectedValue(new Error("503 down"));
    render(<MCPPage />);
    await waitFor(() => screen.getByText(/show 2 tools/i));
    await act(async () => {
      await userEvent.click(screen.getByText(/show 2 tools/i));
    });
    await waitFor(() => expect(screen.getByText(/load failed: 503 down/i)).toBeInTheDocument());
  });

  it("renders a top-level error when /mcp/servers itself fails", async () => {
    vi.spyOn(api, "listMcpServers").mockRejectedValue(new Error("nope"));
    render(<MCPPage />);
    await waitFor(() =>
      expect(screen.getByText(/Couldn't load MCP servers/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/nope/)).toBeInTheDocument();
  });

  it("singular vs plural tool count is rendered correctly", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ name: "single", tool_count: 1, tools: ["solo"] }),
    ]);
    render(<MCPPage />);
    await waitFor(() => screen.getByText("1 tool"));
    expect(screen.getByText(/show 1 tool$/i)).toBeInTheDocument();
  });
});
