import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { AdminPage } from "./AdminPage";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    adminAccounting: vi.fn(),
    adminActivity: vi.fn(),
    setUserLimit: vi.fn(),
  },
}));

const ACCT = {
  users: [
    {
      // explicit $10 override
      email: "alice@x.com", tasks: 3, settled: 3, usd: 5.25,
      input_tokens: 1000, output_tokens: 400, wall_seconds: 12,
      spent_today_usd: 5.25, daily_limit_usd: 10, effective_limit_usd: 10,
      last_login_at: Date.now() / 1000 - 120, login_count: 4,
    },
    {
      // no explicit override → falls back to the $10 default; over it
      email: "bob@x.com", tasks: 1, settled: 1, usd: 9.9,
      input_tokens: 200, output_tokens: 80, wall_seconds: 4,
      spent_today_usd: 9.9, daily_limit_usd: null, effective_limit_usd: 10,
      last_login_at: null, login_count: 0,
    },
  ],
  day_start_utc: 1719792000,
  default_daily_limit_usd: 10,
};

const ACTIVITY = {
  activity: [
    {
      kind: "task", task_id: "t1", owner_email: "alice@x.com", agent: "sysadmin",
      status: "success", submitted_at: 1719792100, cost_usd: 2.0,
      natural_language: "deploy the thing",
    },
    {
      kind: "login", task_id: null, owner_email: "bob@x.com", agent: null,
      status: "login", submitted_at: 1719792050, cost_usd: null,
      natural_language: "signed in via google",
    },
  ],
};

describe("AdminPage", () => {
  beforeEach(() => {
    (api.adminAccounting as ReturnType<typeof vi.fn>).mockResolvedValue(ACCT);
    (api.adminActivity as ReturnType<typeof vi.fn>).mockResolvedValue(ACTIVITY);
    (api.setUserLimit as ReturnType<typeof vi.fn>).mockResolvedValue({ email: "alice@x.com", daily_limit_usd: 20 });
  });
  afterEach(() => vi.clearAllMocks());

  it("renders per-user rows with cost + limit", async () => {
    render(<AdminPage />);
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-user-row")).toHaveLength(2);
    });
    const emails = screen.getAllByTestId("admin-user-row").map((r) => r.getAttribute("data-email"));
    expect(emails).toEqual(["alice@x.com", "bob@x.com"]);
  });

  it("shows the default daily limit + (default) hint for non-overridden users", async () => {
    render(<AdminPage />);
    await waitFor(() => expect(screen.getByTestId("admin-default-limit")).toBeInTheDocument());
    // bob has no explicit override → his row shows "(default)".
    const bobRow = screen.getAllByTestId("admin-user-row").find(
      (r) => r.getAttribute("data-email") === "bob@x.com",
    )!;
    expect(bobRow.textContent).toContain("(default)");
  });

  it("renders the activity feed with task + login events", async () => {
    render(<AdminPage />);
    await waitFor(() => {
      expect(screen.getByTestId("admin-activity")).toBeInTheDocument();
      expect(screen.getByText("deploy the thing")).toBeInTheDocument();
      // login event renders distinctly (kind=login row)
      expect(screen.getByText("signed in via google")).toBeInTheDocument();
    });
    const feed = screen.getByTestId("admin-activity");
    expect(feed.querySelector("[data-kind='login']")).not.toBeNull();
    expect(feed.querySelector("[data-kind='task']")).not.toBeNull();
  });

  it("edits a daily limit and PUTs it", async () => {
    render(<AdminPage />);
    await waitFor(() => expect(screen.getAllByTestId("admin-user-row").length).toBeGreaterThan(0));

    const aliceRow = screen.getAllByTestId("admin-user-row").find(
      (r) => r.getAttribute("data-email") === "alice@x.com",
    )!;
    // Click the edit affordance in that row.
    const editBtn = aliceRow.querySelector("[data-testid='admin-limit-edit']") as HTMLElement;
    fireEvent.click(editBtn);

    const input = screen.getByTestId("admin-limit-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "20" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(api.setUserLimit).toHaveBeenCalledWith("alice@x.com", 20);
    });
  });

  it("shows an error panel when accounting fails", async () => {
    (api.adminAccounting as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("403 super-admin only"));
    render(<AdminPage />);
    await waitFor(() => {
      expect(screen.getByTestId("admin-error")).toBeInTheDocument();
    });
  });
});
