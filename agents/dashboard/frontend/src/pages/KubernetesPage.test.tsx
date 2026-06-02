import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KubernetesPage, parseKubectlTable } from "./KubernetesPage";
import { api } from "../api";

const PODS_OUTPUT = [
  "NAME                READY   STATUS              RESTARTS   AGE   IP             NODE",
  "olympus-abc-123     1/1     Running             0          1h    10.0.0.1       node-a",
  "buggy-def-456       0/1     CrashLoopBackOff    7          1h    10.0.0.2       node-b",
].join("\n");

const NODES_OUTPUT = [
  "NAME       STATUS   ROLES                  AGE   VERSION",
  "node-a     Ready    control-plane,master   2d    v1.30.0",
].join("\n");

const EVENTS_OUTPUT = [
  "LAST SEEN   TYPE      REASON   OBJECT     MESSAGE",
  "5m          Warning   Failed   pod/foo    something broke",
].join("\n");

beforeEach(() => {
  vi.restoreAllMocks();
  window.alert = vi.fn();
});
afterEach(() => {
  cleanup();
});

describe("parseKubectlTable", () => {
  it("parses a kubectl get pods table by column position", () => {
    const rows = parseKubectlTable(PODS_OUTPUT);
    expect(rows.length).toBe(2);
    expect(rows[0].NAME).toBe("olympus-abc-123");
    expect(rows[0].STATUS).toBe("Running");
    expect(rows[0].READY).toBe("1/1");
    expect(rows[0].NODE).toBe("node-a");
    expect(rows[1].NAME).toBe("buggy-def-456");
    expect(rows[1].STATUS).toBe("CrashLoopBackOff");
  });

  it("returns empty array on empty input", () => {
    expect(parseKubectlTable("")).toEqual([]);
    expect(parseKubectlTable("   ")).toEqual([]);
  });

  it("strips blank lines", () => {
    const raw = PODS_OUTPUT + "\n\n";
    expect(parseKubectlTable(raw).length).toBe(2);
  });
});

describe("KubernetesPage — pods table", () => {
  it("loads pods on mount, renders one row per pod with Status badge tones", async () => {
    const invokeSpy = vi
      .spyOn(api, "invokeTool")
      .mockImplementation(async (_agent: string, tool: string) => {
        if (tool === "get_pods") {
          return { task_id: "t", agent: "sysadmin", tool, result: PODS_OUTPUT };
        }
        return { task_id: "t", agent: "sysadmin", tool, result: "" };
      });

    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));
    expect(screen.getByText("olympus-abc-123")).toBeInTheDocument();
    expect(screen.getByText("buggy-def-456")).toBeInTheDocument();

    // Status badges by tone.
    const runningBadge = screen.getByText("Running");
    expect(runningBadge).toHaveClass("text-accent-green");
    const crashBadge = screen.getByText("CrashLoopBackOff");
    expect(crashBadge).toHaveClass("text-accent-red");

    // Action buttons present.
    const logsButtons = screen.getAllByRole("button", { name: /logs/i });
    expect(logsButtons.length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByRole("button", { name: /describe/i }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByRole("button", { name: /delete/i }).length).toBeGreaterThanOrEqual(2);

    expect(invokeSpy).toHaveBeenCalledWith("sysadmin", "get_pods", { namespace: "olympus" });
  });

  it("clicking delete + confirm=true → POST delete_pod with correct args", async () => {
    let getPodsCalls = 0;
    const invokeSpy = vi
      .spyOn(api, "invokeTool")
      .mockImplementation(async (_agent: string, tool: string) => {
        if (tool === "get_pods") {
          getPodsCalls++;
          return { task_id: "t", agent: "sysadmin", tool, result: PODS_OUTPUT };
        }
        return { task_id: "t", agent: "sysadmin", tool, result: "" };
      });
    window.confirm = vi.fn().mockReturnValue(true);

    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    const deleteButtons = screen.getAllByRole("button", { name: /delete/i });
    await act(async () => {
      await userEvent.click(deleteButtons[0]);
    });

    expect(window.confirm).toHaveBeenCalled();
    expect(invokeSpy).toHaveBeenCalledWith("sysadmin", "delete_pod", {
      name: "olympus-abc-123",
      namespace: "olympus",
    });
    // Refresh fired after delete → get_pods called again.
    await waitFor(() => expect(getPodsCalls).toBeGreaterThan(1));
  });

  it("clicking delete + confirm=false → no delete_pod POST", async () => {
    const invokeSpy = vi
      .spyOn(api, "invokeTool")
      .mockImplementation(async (_agent: string, tool: string) => ({
        task_id: "t",
        agent: "sysadmin",
        tool,
        result: PODS_OUTPUT,
      }));
    window.confirm = vi.fn().mockReturnValue(false);

    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    const deleteButtons = screen.getAllByRole("button", { name: /delete/i });
    await act(async () => {
      await userEvent.click(deleteButtons[0]);
    });

    const deleteCall = invokeSpy.mock.calls.find((c) => c[1] === "delete_pod");
    expect(deleteCall).toBeUndefined();
  });
});

describe("KubernetesPage — pod row actions", () => {
  it("clicking 'logs' → fetches get_logs and opens a modal with the output", async () => {
    const invokeSpy = vi
      .spyOn(api, "invokeTool")
      .mockImplementation(async (_a: string, tool: string) => ({
        task_id: "t", agent: "sysadmin", tool,
        result: tool === "get_pods" ? PODS_OUTPUT : "2026-01-01 [INFO] hello from logs",
      }));
    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    const logsButtons = screen.getAllByRole("button", { name: /logs/i });
    await act(async () => { await userEvent.click(logsButtons[0]); });

    expect(invokeSpy).toHaveBeenCalledWith("sysadmin", "get_logs", {
      pod: "olympus-abc-123", namespace: "olympus", tail_lines: 200,
    });
    await waitFor(() => expect(screen.getByText(/hello from logs/)).toBeInTheDocument());
  });

  it("logs fetch failure → modal still opens with the error string", async () => {
    vi.spyOn(api, "invokeTool").mockImplementation(async (_a: string, tool: string) => {
      if (tool === "get_pods") return { task_id: "t", agent: "sysadmin", tool, result: PODS_OUTPUT };
      throw new Error("no such pod");
    });
    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    await act(async () => {
      await userEvent.click(screen.getAllByRole("button", { name: /logs/i })[0]);
    });
    await waitFor(() => expect(screen.getByText(/no such pod/)).toBeInTheDocument());
  });

  it("clicking 'describe' → fetches describe_pod and opens a modal", async () => {
    const invokeSpy = vi
      .spyOn(api, "invokeTool")
      .mockImplementation(async (_a: string, tool: string) => ({
        task_id: "t", agent: "sysadmin", tool,
        result: tool === "get_pods" ? PODS_OUTPUT : "Name: olympus-abc-123\nStatus: Running",
      }));
    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    await act(async () => {
      await userEvent.click(screen.getAllByRole("button", { name: /describe/i })[0]);
    });

    expect(invokeSpy).toHaveBeenCalledWith("sysadmin", "describe_pod", {
      name: "olympus-abc-123", namespace: "olympus",
    });
    await waitFor(() => expect(screen.getByText(/Status: Running/)).toBeInTheDocument());
  });

  it("delete failure → alert() with the error message", async () => {
    vi.spyOn(api, "invokeTool").mockImplementation(async (_a: string, tool: string) => {
      if (tool === "get_pods") return { task_id: "t", agent: "sysadmin", tool, result: PODS_OUTPUT };
      throw new Error("denied");
    });
    window.confirm = vi.fn().mockReturnValue(true);
    const alertSpy = window.alert as ReturnType<typeof vi.fn>;
    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    await act(async () => {
      await userEvent.click(screen.getAllByRole("button", { name: /delete/i })[0]);
    });
    await waitFor(() => expect(alertSpy).toHaveBeenCalledWith(expect.stringMatching(/delete_pod failed: denied/)));
  });
});

describe("KubernetesPage — nodes / events error paths", () => {
  it("nodes fetch failure surfaces the error message", async () => {
    vi.spyOn(api, "invokeTool").mockImplementation(async (_a: string, tool: string) => {
      if (tool === "get_pods") return { task_id: "t", agent: "sysadmin", tool, result: PODS_OUTPUT };
      if (tool === "get_nodes") throw new Error("503 down");
      return { task_id: "t", agent: "sysadmin", tool, result: "" };
    });
    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    await userEvent.click(screen.getByRole("button", { name: /^\s*Nodes\s*$/i }));
    await waitFor(() => expect(screen.getByText(/error: 503 down/i)).toBeInTheDocument());
  });

  it("events fetch failure surfaces the error message", async () => {
    vi.spyOn(api, "invokeTool").mockImplementation(async (_a: string, tool: string) => {
      if (tool === "get_pods") return { task_id: "t", agent: "sysadmin", tool, result: PODS_OUTPUT };
      if (tool === "get_events") throw new Error("events fetch crashed");
      return { task_id: "t", agent: "sysadmin", tool, result: "" };
    });
    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    await userEvent.click(screen.getByRole("button", { name: /^\s*Events\s*$/i }));
    await waitFor(() => expect(screen.getByText(/error: events fetch crashed/i)).toBeInTheDocument());
  });
});

describe("KubernetesPage — tab strip", () => {
  it("clicking Nodes switches the rendered table", async () => {
    vi.spyOn(api, "invokeTool").mockImplementation(async (_agent: string, tool: string) => {
      if (tool === "get_pods") return { task_id: "t", agent: "sysadmin", tool, result: PODS_OUTPUT };
      if (tool === "get_nodes") return { task_id: "t", agent: "sysadmin", tool, result: NODES_OUTPUT };
      return { task_id: "t", agent: "sysadmin", tool, result: "" };
    });

    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    // The tab label "Nodes" is visible in the tab strip even before clicking.
    const nodesTab = screen.getByRole("button", { name: /^\s*Nodes\s*$/i });
    await userEvent.click(nodesTab);

    await waitFor(() => expect(screen.getByText("node-a")).toBeInTheDocument());
    // Pod no longer shown.
    expect(screen.queryByText("olympus-abc-123")).not.toBeInTheDocument();
  });

  it("clicking Events switches to events table", async () => {
    vi.spyOn(api, "invokeTool").mockImplementation(async (_agent: string, tool: string) => {
      if (tool === "get_pods") return { task_id: "t", agent: "sysadmin", tool, result: PODS_OUTPUT };
      if (tool === "get_events") return { task_id: "t", agent: "sysadmin", tool, result: EVENTS_OUTPUT };
      return { task_id: "t", agent: "sysadmin", tool, result: "" };
    });

    render(<KubernetesPage />);
    await waitFor(() => screen.getByText("olympus-abc-123"));

    const eventsTab = screen.getByRole("button", { name: /^\s*Events\s*$/i });
    await userEvent.click(eventsTab);

    await waitFor(() => expect(screen.getByText(/something broke/i)).toBeInTheDocument());
  });
});
