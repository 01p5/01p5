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


describe("MCPPage — NetDB card", () => {
  it("renders the NetDB connect card when no netdb server is wired", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    render(<MCPPage />);
    await waitFor(() => screen.getByTestId("netdb-card"));
    expect(screen.getByText(/NetDB integration/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/10\.0\.3\.5/)).toBeInTheDocument();
  });

  it("hides the NetDB card once a server named 'netdb' is wired", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ name: "netdb", target_agent: "sysadmin", tool_count: 32 }),
    ]);
    render(<MCPPage />);
    await waitFor(() => screen.getByText("netdb"));
    expect(screen.queryByTestId("netdb-card")).toBeNull();
  });

  it("connect normalizes a bare IP into http://<ip>:8080/mcp and POSTs the right shape", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const addSpy = vi.spyOn(api, "addMcpServer").mockResolvedValue(
      SERVER({ name: "netdb", target_agent: "sysadmin", tool_count: 32 }),
    );

    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("netdb-card"));
    await act(async () => {
      await userEvent.type(within(card).getByRole("textbox", { name: /netdb host/i }), "10.0.3.5");
      await userEvent.click(within(card).getByRole("button", { name: /connect/i }));
    });

    expect(addSpy).toHaveBeenCalledTimes(1);
    const req = addSpy.mock.calls[0]![0];
    expect(req.name).toBe("netdb");
    expect(req.target_agent).toBe("sysadmin");
    expect(req.transport).toBe("http");
    expect(req.url).toBe("http://10.0.3.5:8080/mcp");
    // Destructive tools list is the union of all netdb mutating verbs.
    expect(req.destructive).toContain("create_host");
    expect(req.destructive).toContain("delete_zone");
  });

  it("accepts an explicit URL with port + path verbatim", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const addSpy = vi.spyOn(api, "addMcpServer").mockResolvedValue(
      SERVER({ name: "netdb" }),
    );

    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("netdb-card"));
    await act(async () => {
      await userEvent.type(within(card).getByRole("textbox", { name: /netdb host/i }), "http://netdb.example.com:9999/mcp");
      await userEvent.click(within(card).getByRole("button", { name: /connect/i }));
    });

    expect(addSpy.mock.calls[0]![0].url).toBe("http://netdb.example.com:9999/mcp");
  });

  it("surfaces backend errors inline without re-submitting", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    vi.spyOn(api, "addMcpServer").mockRejectedValue(new Error("ECONNREFUSED"));

    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("netdb-card"));
    await act(async () => {
      await userEvent.type(within(card).getByRole("textbox", { name: /netdb host/i }), "10.0.3.99");
      await userEvent.click(within(card).getByRole("button", { name: /connect/i }));
    });

    await waitFor(() =>
      expect(screen.getByText(/connect failed: ECONNREFUSED/i)).toBeInTheDocument(),
    );
  });
});


describe("MCPPage — HPC integration card", () => {
  it("renders when neither gpu-mcp nor slurm-mcp are wired", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("hpc-card"));
    expect(within(card).getByText(/HPC integration/i)).toBeInTheDocument();
    expect(within(card).getByText(/gpu-mcp pending/i)).toBeInTheDocument();
    expect(within(card).getByText(/slurm-mcp pending/i)).toBeInTheDocument();
  });

  it("hides itself once both HPC MCPs are connected", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ name: "gpu-mcp", target_agent: "hpc", status: "connected" }),
      SERVER({ name: "slurm-mcp", target_agent: "hpc", status: "connected" }),
    ]);
    render(<MCPPage />);
    await waitFor(() => screen.getAllByText("gpu-mcp"));
    expect(screen.queryByTestId("hpc-card")).toBeNull();
  });

  it("connect button posts BOTH stdio servers targeting the hpc agent", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const addSpy = vi.spyOn(api, "addMcpServer").mockResolvedValue(
      SERVER({ name: "gpu-mcp", target_agent: "hpc", tool_count: 10 }),
    );

    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("hpc-card"));
    await act(async () => {
      await userEvent.click(within(card).getByRole("button", { name: /connect both/i }));
    });

    // Two POSTs, one per server, both target_agent=hpc, both stdio.
    expect(addSpy).toHaveBeenCalledTimes(2);
    const calls = addSpy.mock.calls.map((c) => c[0]);
    const names = calls.map((c) => c.name).sort();
    expect(names).toEqual(["gpu-mcp", "slurm-mcp"]);
    expect(calls.every((c) => c.target_agent === "hpc")).toBe(true);
    expect(calls.every((c) => c.transport === "stdio")).toBe(true);
    // gpu-mcp ships no destructive tools; slurm-mcp's destructive set
    // is allowlisted explicitly so the Olympus approval queue gates
    // jobs_cancel etc. even if the server forgot to label them.
    const slurmCall = calls.find((c) => c.name === "slurm-mcp")!;
    expect(slurmCall.destructive).toContain("jobs_cancel");
    expect(slurmCall.destructive).toContain("jobs_hold");
  });

  it("advanced section lets the user override binary names", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    const addSpy = vi.spyOn(api, "addMcpServer").mockResolvedValue(SERVER({ name: "gpu-mcp" }));

    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("hpc-card"));
    await act(async () => {
      await userEvent.click(within(card).getByRole("button", { name: /show advanced/i }));
    });
    const gpuInput = card.querySelector(".hpc-gpu-cmd") as HTMLInputElement;
    await act(async () => {
      await userEvent.clear(gpuInput);
      await userEvent.type(gpuInput, "/opt/local/bin/gpu-mcp");
      await userEvent.click(within(card).getByRole("button", { name: /connect both/i }));
    });

    const gpuCall = addSpy.mock.calls.map((c) => c[0]).find((c) => c.name === "gpu-mcp")!;
    expect(gpuCall.command).toBe("/opt/local/bin/gpu-mcp");
  });

  it("skips an already-connected MCP and reports it instead of double-registering", async () => {
    // gpu-mcp is already up — slurm-mcp isn't. The card stays
    // visible (because both must be connected to hide it) and the
    // submit path skips the live one rather than POSTing a dup.
    vi.spyOn(api, "listMcpServers").mockResolvedValue([
      SERVER({ name: "gpu-mcp", target_agent: "hpc", status: "connected", tool_count: 10 }),
    ]);
    const addSpy = vi.spyOn(api, "addMcpServer").mockResolvedValue(
      SERVER({ name: "slurm-mcp", target_agent: "hpc", tool_count: 18 }),
    );
    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("hpc-card"));
    await act(async () => {
      await userEvent.click(within(card).getByRole("button", { name: /connect both/i }));
    });
    // Only slurm-mcp gets registered; gpu-mcp is acknowledged as
    // already connected without a re-POST.
    expect(addSpy).toHaveBeenCalledTimes(1);
    expect(addSpy.mock.calls[0]![0].name).toBe("slurm-mcp");
    expect(within(card).getByText(/gpu-mcp: already connected/i)).toBeInTheDocument();
  });

  it("toggling the advanced section twice closes it again", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("hpc-card"));
    // Initially the binary inputs aren't visible.
    expect(card.querySelector(".hpc-gpu-cmd")).toBeNull();
    await act(async () => {
      await userEvent.click(within(card).getByRole("button", { name: /show advanced/i }));
    });
    expect(card.querySelector(".hpc-gpu-cmd")).not.toBeNull();
    // The label flips to "hide advanced" once open; click closes it.
    await act(async () => {
      await userEvent.click(within(card).getByRole("button", { name: /hide advanced/i }));
    });
    expect(card.querySelector(".hpc-gpu-cmd")).toBeNull();
  });

  it("partial failure surfaces per-server: gpu ok, slurm error", async () => {
    vi.spyOn(api, "listMcpServers").mockResolvedValue([]);
    vi.spyOn(api, "addMcpServer").mockImplementation(async (req) => {
      if (req.name === "slurm-mcp") throw new Error("ENOENT: slurm-mcp not on PATH");
      return SERVER({ name: req.name, tool_count: 10 });
    });
    render(<MCPPage />);
    const card = await waitFor(() => screen.getByTestId("hpc-card"));
    await act(async () => {
      await userEvent.click(within(card).getByRole("button", { name: /connect both/i }));
    });
    await waitFor(() =>
      expect(within(card).getByText(/gpu-mcp: connected/i)).toBeInTheDocument(),
    );
    expect(within(card).getByText(/slurm-mcp: ENOENT: slurm-mcp not on PATH/i)).toBeInTheDocument();
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
