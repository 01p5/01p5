import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { AuditPanel } from "./AuditPanel";
import { api } from "../api";
import type { AuditRecord } from "../types";

const RECS: AuditRecord[] = [
  {
    ts: 1_700_000_000,
    task_id: "t1",
    agent: "sysadmin",
    tool: "get_pods",
    args: { ns: "default" },
    result: "...",
    approved: null,
  },
  {
    ts: 1_700_000_100,
    task_id: "t2",
    agent: "terraform",
    tool: "tf_apply",
    args: { working_dir: "/x" },
    result: "ok",
    approved: true,
  },
  {
    ts: 1_700_000_200,
    task_id: "t3",
    agent: "ansible",
    tool: "run_playbook",
    args: { playbook: "p.yml" },
    result: "denied",
    approved: false,
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("AuditPanel", () => {
  it("renders empty state when no audit entries", async () => {
    vi.spyOn(api, "audit").mockResolvedValue([]);
    render(<AuditPanel />);
    await waitFor(() => expect(screen.getByText(/no audit entries yet/i)).toBeInTheDocument());
  });

  it("renders one .audit-row per record", async () => {
    vi.spyOn(api, "audit").mockResolvedValue(RECS);
    const { container } = render(<AuditPanel />);
    await waitFor(() => {
      expect(container.querySelectorAll(".audit-row").length).toBe(3);
    });
  });

  it("applies the approved-class to each row based on approved flag", async () => {
    vi.spyOn(api, "audit").mockResolvedValue(RECS);
    const { container } = render(<AuditPanel />);
    await waitFor(() => {
      expect(container.querySelectorAll(".audit-row").length).toBe(3);
    });
    // Rows render newest-first (reverse). Inside each row the leading
    // [bracket] span carries the class.
    const rows = Array.from(container.querySelectorAll(".audit-row"));
    // RECS were appended in order [null, true, false]; after reverse it
    // becomes [false, true, null].
    const classes = rows.map((r) => {
      const first = r.querySelector("span");
      return first?.className ?? "";
    });
    expect(classes[0]).toContain("approved-false");
    expect(classes[1]).toContain("approved-true");
    expect(classes[2]).toContain("approved-null");
  });

  it("renders tool name + agent text", async () => {
    vi.spyOn(api, "audit").mockResolvedValue([RECS[0]]);
    render(<AuditPanel />);
    await waitFor(() => {
      expect(screen.getByText("sysadmin")).toBeInTheDocument();
      expect(screen.getByText("get_pods")).toBeInTheDocument();
    });
  });

  it("renders args when args is a string (passthrough)", async () => {
    const rec: AuditRecord = { ...RECS[0], args: "raw-arg-string" };
    vi.spyOn(api, "audit").mockResolvedValue([rec]);
    render(<AuditPanel />);
    await waitFor(() => {
      expect(screen.getByText("raw-arg-string")).toBeInTheDocument();
    });
  });
});
