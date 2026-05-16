import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RollbackPanel, RollbackRow } from "./RollbackPanel";
import { api } from "../api";
import type { RollbackEntry } from "../types";

const ENTRY = (over: Partial<RollbackEntry> = {}): RollbackEntry => ({
  rollback_id: "RB1",
  task_id: "task-abc-123",
  agent: "programmer",
  forward_tool: "write_file",
  forward_args: { path: "/tmp/x", content: "new" },
  inverse_tool: "write_file",
  inverse_args: { path: "/tmp/x", content: "prior" },
  description: "restore /tmp/x",
  snapshot: { prior_exists: true },
  ts: 1_700_000_000,
  executed: false,
  executed_ts: null,
  executed_result: null,
  ...over,
});

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("RollbackPanel", () => {
  it("renders the empty state when no rollbacks", async () => {
    vi.spyOn(api, "listRollbacks").mockResolvedValue([]);
    render(<RollbackPanel />);
    await waitFor(() =>
      expect(screen.getByText(/no captured rollbacks yet/i)).toBeInTheDocument(),
    );
  });

  it("renders one row per captured rollback with agent → inverse_tool header", async () => {
    vi.spyOn(api, "listRollbacks").mockResolvedValue([
      ENTRY({ rollback_id: "RB1", description: "restore /tmp/a" }),
      ENTRY({ rollback_id: "RB2", description: "restore /tmp/b", agent: "sysadmin", inverse_tool: "delete_pod" }),
    ]);
    const { container } = render(<RollbackPanel />);
    await waitFor(() =>
      expect(screen.getByText("restore /tmp/a")).toBeInTheDocument(),
    );
    expect(screen.getByText("restore /tmp/b")).toBeInTheDocument();
    expect(container.querySelectorAll(".rollback-row").length).toBe(2);
    expect(screen.getByText(/programmer → write_file/)).toBeInTheDocument();
    expect(screen.getByText(/sysadmin → delete_pod/)).toBeInTheDocument();
  });

  it("shows the captured count in the header", async () => {
    vi.spyOn(api, "listRollbacks").mockResolvedValue([ENTRY()]);
    render(<RollbackPanel />);
    await waitFor(() => expect(screen.getByText("1 captured")).toBeInTheDocument());
  });

  it("greys out an already-executed entry (no Undo button)", async () => {
    vi.spyOn(api, "listRollbacks").mockResolvedValue([
      ENTRY({ executed: true, executed_ts: 1_700_000_010 }),
    ]);
    const { container } = render(<RollbackPanel />);
    await waitFor(() =>
      expect(screen.getByText("executed")).toBeInTheDocument(),
    );
    const row = container.querySelector(".rollback-row");
    expect(row?.getAttribute("data-executed")).toBe("1");
    expect(screen.queryByRole("button", { name: /undo:/i })).toBeNull();
  });
});

describe("RollbackRow", () => {
  it("fires onExecute(rollback_id) when Undo is clicked", async () => {
    const fire = vi.fn().mockResolvedValue({
      rollback_id: "RB1", task_id: "rollback:RB1",
      agent: "programmer", tool: "write_file",
      result: "wrote 5 bytes to /tmp/x",
    });
    const changed = vi.fn();
    render(<RollbackRow entry={ENTRY()} onChanged={changed} onExecute={fire} />);

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /undo:/i }));
    });
    expect(fire).toHaveBeenCalledWith("RB1");
    await waitFor(() =>
      expect(screen.getByText(/wrote 5 bytes to \/tmp\/x/i)).toBeInTheDocument(),
    );
    expect(changed).toHaveBeenCalled();
  });

  it("shows the error message when execute fails", async () => {
    const fire = vi.fn().mockRejectedValue(new Error("409 Conflict"));
    render(<RollbackRow entry={ENTRY()} onChanged={() => {}} onExecute={fire} />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /undo:/i }));
    });
    await waitFor(() => expect(screen.getByText(/409 Conflict/i)).toBeInTheDocument());
  });

  it("disables the button while the request is in flight", async () => {
    let resolveIt: (v: unknown) => void = () => {};
    const pending = new Promise((r) => { resolveIt = r; });
    const fire = vi.fn().mockReturnValue(pending);
    render(<RollbackRow entry={ENTRY()} onChanged={() => {}} onExecute={fire as unknown as typeof api.executeRollback} />);

    const btn = screen.getByRole("button", { name: /undo:/i });
    await act(async () => {
      await userEvent.click(btn);
    });
    expect(btn).toBeDisabled();
    expect(btn.textContent).toMatch(/rolling back/i);

    // Resolve so React's act() cleanup is happy.
    await act(async () => {
      resolveIt({
        rollback_id: "RB1", task_id: "x", agent: "a", tool: "t", result: "ok",
      });
      await pending;
    });
  });

  it("a non-string result is JSON-stringified for display", async () => {
    const fire = vi.fn().mockResolvedValue({
      rollback_id: "RB1", task_id: "x", agent: "a", tool: "t",
      result: { ok: true, bytes: 42 },
    });
    render(<RollbackRow entry={ENTRY()} onChanged={() => {}} onExecute={fire} />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /undo:/i }));
    });
    await waitFor(() =>
      expect(screen.getByText(/"bytes":42/)).toBeInTheDocument(),
    );
  });

  it("renders the executed badge + skips the button when entry.executed", () => {
    render(
      <RollbackRow
        entry={ENTRY({ executed: true, executed_result: "wrote 5 bytes" })}
        onChanged={() => {}}
        onExecute={vi.fn()}
      />,
    );
    expect(screen.getByText("executed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /undo:/i })).toBeNull();
  });
});
