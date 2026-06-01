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

  it("shows '—' when a user has no daily limit at all", async () => {
    (api.adminAccounting as ReturnType<typeof vi.fn>).mockResolvedValue({
      users: [{
        email: "nolimit@x.com", tasks: 0, settled: 0, usd: 0,
        input_tokens: 0, output_tokens: 0, wall_seconds: 0,
        spent_today_usd: 0, daily_limit_usd: null, effective_limit_usd: null,
        last_login_at: null, login_count: 0,
      }],
      day_start_utc: 1719792000,
      default_daily_limit_usd: null,  // no default → no banner, "—" limit
    });
    render(<AdminPage />);
    await waitFor(() => expect(screen.getAllByTestId("admin-user-row").length).toBe(1));
    expect(screen.queryByTestId("admin-default-limit")).not.toBeInTheDocument();
    const row = screen.getByTestId("admin-user-row");
    expect(row.querySelector("[data-testid='admin-limit-edit']")!.textContent).toContain("—");
  });

  it("rejects an invalid (negative) limit without calling the API", async () => {
    render(<AdminPage />);
    await waitFor(() => expect(screen.getAllByTestId("admin-user-row").length).toBeGreaterThan(0));
    const row = screen.getAllByTestId("admin-user-row")[0];
    fireEvent.click(row.querySelector("[data-testid='admin-limit-edit']") as HTMLElement);
    const input = screen.getByTestId("admin-limit-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "-5" } });
    fireEvent.keyDown(input, { key: "Enter" });
    // invalid → guard returns early, editor stays open, no PUT
    await new Promise((r) => setTimeout(r, 20));
    expect(api.setUserLimit).not.toHaveBeenCalled();
    expect(screen.getByTestId("admin-limit-input")).toBeInTheDocument();
  });

  it("Escape cancels the limit editor", async () => {
    render(<AdminPage />);
    await waitFor(() => expect(screen.getAllByTestId("admin-user-row").length).toBeGreaterThan(0));
    const row = screen.getAllByTestId("admin-user-row")[0];
    fireEvent.click(row.querySelector("[data-testid='admin-limit-edit']") as HTMLElement);
    const input = screen.getByTestId("admin-limit-input");
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("admin-limit-input")).not.toBeInTheDocument());
    expect(api.setUserLimit).not.toHaveBeenCalled();
  });

  it("refresh button re-fetches accounting", async () => {
    render(<AdminPage />);
    await waitFor(() => expect(screen.getAllByTestId("admin-user-row").length).toBeGreaterThan(0));
    const before = (api.adminAccounting as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.click(screen.getByLabelText("Refresh"));
    await waitFor(() =>
      expect((api.adminAccounting as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(before),
    );
  });

  it("formats last-login across all relative-time buckets", async () => {
    const now = Date.now() / 1000;
    const mk = (email: string, ago: number | null) => ({
      email, tasks: 0, settled: 0, usd: 0, input_tokens: 0, output_tokens: 0,
      wall_seconds: 0, spent_today_usd: 0, daily_limit_usd: null,
      effective_limit_usd: null, last_login_at: ago == null ? null : now - ago,
      login_count: ago == null ? 0 : 1,
    });
    (api.adminAccounting as ReturnType<typeof vi.fn>).mockResolvedValue({
      users: [mk("a@x.com", 5), mk("b@x.com", 7200), mk("c@x.com", 200000), mk("d@x.com", null)],
      day_start_utc: 1719792000, default_daily_limit_usd: null,
    });
    render(<AdminPage />);
    await waitFor(() => expect(screen.getAllByTestId("admin-user-row").length).toBe(4));
    const table = screen.getByTestId("admin-users-table");
    expect(table.textContent).toContain("just now");
    expect(table.textContent).toContain("2h ago");
    expect(table.textContent).toContain("2d ago");
  });

  it("shows an error panel when accounting fails", async () => {
    (api.adminAccounting as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("403 super-admin only"));
    render(<AdminPage />);
    await waitFor(() => {
      expect(screen.getByTestId("admin-error")).toBeInTheDocument();
    });
  });
});
