import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act, within } from "@testing-library/react";
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


describe("MCPPage — transport prefix", () => {
  it("stdio servers render with a $ shell-prompt prefix", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ command: "python3 server.py" }),
    ]);
    const { container } = render(<MCPPage />);
    await waitFor(() =>
      expect(container.querySelector('[data-transport="stdio"]')).not.toBeNull(),
    );
    const row = container.querySelector('[data-transport="stdio"]')!;
    expect(row.textContent).toMatch(/\$\s*python3 server\.py/);
  });

  it("HTTP servers render with a → endpoint-pointer prefix", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({
        name: "github",
        command: "HTTP https://mcp.example.com/github",
      }),
    ]);
    const { container } = render(<MCPPage />);
    await waitFor(() =>
      expect(container.querySelector('[data-transport="http"]')).not.toBeNull(),
    );
    const row = container.querySelector('[data-transport="http"]')!;
    // The `→` prefix replaces the shell `$` for HTTP — a URL with a
    // `$` prompt would be misleading visual grammar.
    expect(row.textContent).toMatch(/→\s*HTTP https:\/\/mcp\.example\.com\/github/);
    expect(row.textContent).not.toMatch(/\$\s*HTTP/);
  });

  it("server with no command renders without the prefix row entirely", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ command: null }),
    ]);
    const { container } = render(<MCPPage />);
    await waitFor(() =>
      expect(container.querySelectorAll(".mcp-server-card").length).toBe(1),
    );
    // Neither stdio nor http transport row present.
    expect(container.querySelector('[data-transport]')).toBeNull();
  });
});


describe("MCPPage — disconnect server", () => {
  it("disconnect button posts DELETE and refreshes the list", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ name: "ext", tool_count: 0, tools: [] }),
    ]);
    const delSpy = vi.spyOn(api, "deleteMcpServer").mockResolvedValue({
      removed: true, name: "ext",
    });
    vi.stubGlobal("confirm", () => true);

    render(<MCPPage />);
    await waitFor(() => screen.getByText("ext"));

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /disconnect ext/i }));
    });

    expect(delSpy).toHaveBeenCalledWith("ext");
  });

  it("cancelled confirm dialog does NOT call delete", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([SERVER({ name: "ext" })]);
    const delSpy = vi.spyOn(api, "deleteMcpServer").mockResolvedValue({
      removed: true, name: "ext",
    });
    vi.stubGlobal("confirm", () => false);

    render(<MCPPage />);
    await waitFor(() => screen.getByText("ext"));

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /disconnect ext/i }));
    });

    expect(delSpy).not.toHaveBeenCalled();
  });

  it("surfaces a failure inline when delete fails", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([SERVER({ name: "ext" })]);
    vi.spyOn(api, "deleteMcpServer").mockRejectedValue(new Error("503 down"));
    vi.stubGlobal("confirm", () => true);

    render(<MCPPage />);
    await waitFor(() => screen.getByText("ext"));

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /disconnect ext/i }));
    });

    await waitFor(() =>
      expect(screen.getByText(/disconnect failed: 503 down/i)).toBeInTheDocument(),
    );
  });
});


describe("MCPPage — add server form", () => {
  it("Add server button toggles the form", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    render(<MCPPage />);
    expect(screen.queryByTestId("add-mcp-form")).toBeNull();

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add server/i }));
    });
    expect(screen.getByTestId("add-mcp-form")).toBeInTheDocument();

    // Click the form's own cancel button (the header toggle also says
    // "cancel" while expanded, so we have to scope to the form).
    const form = screen.getByTestId("add-mcp-form");
    await act(async () => {
      await userEvent.click(within(form).getByRole("button", { name: /cancel/i }));
    });
    expect(screen.queryByTestId("add-mcp-form")).toBeNull();
  });

  it("submit posts a stdio request with parsed args + destructive set", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const addSpy = vi.spyOn(api, "addMcpServer").mockResolvedValue(
      SERVER({ name: "fs", target_agent: "programmer" }),
    );

    render(<MCPPage />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add server/i }));
    });

    const form = screen.getByTestId("add-mcp-form");
    // The Field component nests the label text + asterisk + input
    // inside a single <label>. Reaching inputs by placeholder is
    // more robust than by label text.
    const inputByPlaceholder = (ph: string): HTMLInputElement =>
      within(form).getByPlaceholderText(ph) as HTMLInputElement;

    await act(async () => {
      await userEvent.type(inputByPlaceholder("github"), "fs");
      // target_agent is a <select> defaulting to "programmer" — leave as-is
      await userEvent.type(inputByPlaceholder("python3"), "python3");
      await userEvent.type(
        inputByPlaceholder("-m mymodule --flag value"),
        '-m mymod --flag "two words"',
      );
      await userEvent.type(
        inputByPlaceholder("write_file, delete_branch"),
        "write_file, delete_branch",
      );
    });
    await act(async () => {
      await userEvent.click(within(form).getByRole("button", { name: /register/i }));
    });

    expect(addSpy).toHaveBeenCalledTimes(1);
    const req = addSpy.mock.calls[0]![0];
    expect(req.name).toBe("fs");
    expect(req.target_agent).toBe("programmer");
    expect(req.transport).toBe("stdio");
    expect(req.command).toBe("python3");
    expect(req.args).toEqual(["-m", "mymod", "--flag", "two words"]);
    expect(req.destructive).toEqual(["write_file", "delete_branch"]);
  });

  it("target agent is a dropdown of the four production agents", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const addSpy = vi.spyOn(api, "addMcpServer").mockResolvedValue(
      SERVER({ name: "ks", target_agent: "sysadmin" }),
    );
    render(<MCPPage />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add server/i }));
    });

    const form = screen.getByTestId("add-mcp-form");
    const select = within(form).getByRole("combobox") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toEqual(["programmer", "sysadmin", "terraform", "ansible"]);
    expect(select.value).toBe("programmer");

    await act(async () => {
      await userEvent.selectOptions(select, "sysadmin");
      await userEvent.type(within(form).getByPlaceholderText("github"), "ks");
      await userEvent.type(within(form).getByPlaceholderText("python3"), "echo");
    });
    await act(async () => {
      await userEvent.click(within(form).getByRole("button", { name: /register/i }));
    });
    expect(addSpy.mock.calls[0]![0].target_agent).toBe("sysadmin");
  });

  it("switching to http hides stdio fields and parses headers", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const addSpy = vi.spyOn(api, "addMcpServer").mockResolvedValue(
      SERVER({ name: "remote" }),
    );

    render(<MCPPage />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add server/i }));
    });
    const form = screen.getByTestId("add-mcp-form");
    await act(async () => {
      await userEvent.click(within(form).getByLabelText(/http \(remote\)/i));
    });
    expect(form.querySelector('[data-transport-fields="stdio"]')).toBeNull();
    expect(form.querySelector('[data-transport-fields="http"]')).not.toBeNull();

    const phInput = (ph: string): HTMLInputElement =>
      within(form).getByPlaceholderText(ph) as HTMLInputElement;
    const phArea = (ph: string): HTMLTextAreaElement =>
      within(form).getByPlaceholderText(ph) as HTMLTextAreaElement;

    await act(async () => {
      await userEvent.type(phInput("github"), "remote");
      await userEvent.type(
        phInput("https://mcp.example.com/server"),
        "https://mcp.example.com/x",
      );
      await userEvent.type(
        phArea("Authorization: Bearer ..."),
        "Authorization: Bearer abc{enter}X-Other: yes",
      );
    });
    await act(async () => {
      await userEvent.click(within(form).getByRole("button", { name: /register/i }));
    });

    expect(addSpy).toHaveBeenCalledTimes(1);
    const req = addSpy.mock.calls[0]![0];
    expect(req.transport).toBe("http");
    expect(req.url).toBe("https://mcp.example.com/x");
    expect(req.headers).toEqual({ Authorization: "Bearer abc", "X-Other": "yes" });
    expect(req.command).toBeUndefined();
  });

  it("surfaces backend errors inline without collapsing the form", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    vi.spyOn(api, "addMcpServer").mockRejectedValue(
      new Error("409 Conflict: already registered"),
    );

    render(<MCPPage />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add server/i }));
    });
    const form = screen.getByTestId("add-mcp-form");
    await act(async () => {
      await userEvent.type(within(form).getByPlaceholderText("github"), "dup");
      await userEvent.type(within(form).getByPlaceholderText("python3"), "echo");
    });
    await act(async () => {
      await userEvent.click(within(form).getByRole("button", { name: /register/i }));
    });

    await waitFor(() =>
      expect(screen.getByText(/409 Conflict/i)).toBeInTheDocument(),
    );
    expect(screen.getByTestId("add-mcp-form")).toBeInTheDocument();
  });
});
