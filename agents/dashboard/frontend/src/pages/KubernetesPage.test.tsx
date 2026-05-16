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

    expect(invokeSpy).toHaveBeenCalledWith("sysadmin", "get_pods", { namespace: "default" });
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
      namespace: "default",
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
