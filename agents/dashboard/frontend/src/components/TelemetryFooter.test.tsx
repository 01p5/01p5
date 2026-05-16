import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { TelemetryFooter } from "./TelemetryFooter";
import { api } from "../api";
import type { TelemetryResponse } from "../types";

const EMPTY: TelemetryResponse = {
  totals: { tasks: 0, settled: 0, usd: 0, input_tokens: 0, output_tokens: 0, wall_seconds: 0 },
  by_agent: {},
  by_status: {},
  recent: [],
};

const SAMPLE: TelemetryResponse = {
  totals: {
    tasks: 5, settled: 4,
    usd: 0.0072, input_tokens: 1200, output_tokens: 480,
    wall_seconds: 18.5,
  },
  by_agent: {
    sysadmin: { tasks: 3, usd: 0.0048, input_tokens: 900, output_tokens: 300, wall_seconds: 10.0 },
    programmer: { tasks: 1, usd: 0.0024, input_tokens: 300, output_tokens: 180, wall_seconds: 8.5 },
  },
  by_status: { success: 4, running: 1 },
  recent: [],
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("TelemetryFooter", () => {
  it("renders nothing while telemetry is still loading", () => {
    vi.spyOn(api, "telemetry").mockReturnValue(new Promise(() => {}));  // never resolves
    const { container } = render(<TelemetryFooter />);
    expect(container.querySelector("#telemetry-footer")).toBeNull();
  });

  it("hides when totals.tasks is zero (clean empty state)", async () => {
    vi.spyOn(api, "telemetry").mockResolvedValue(EMPTY);
    const { container } = render(<TelemetryFooter />);
    await waitFor(() => expect(api.telemetry).toHaveBeenCalled());
    expect(container.querySelector("#telemetry-footer")).toBeNull();
  });

  it("renders the totals row when tasks > 0", async () => {
    vi.spyOn(api, "telemetry").mockResolvedValue(SAMPLE);
    render(<TelemetryFooter />);
    await waitFor(() => screen.getByText(/4\/5 tasks/));
    expect(screen.getByText(/\$0\.0072/)).toBeInTheDocument();
    expect(screen.getByText(/1,680 tokens/)).toBeInTheDocument();
    expect(screen.getByText(/18\.5s wall/)).toBeInTheDocument();
  });

  it("shows the per-task average when settled > 1", async () => {
    vi.spyOn(api, "telemetry").mockResolvedValue(SAMPLE);
    const { container } = render(<TelemetryFooter />);
    await waitFor(() =>
      expect(container.querySelector('[data-stat="spend"]')).not.toBeNull(),
    );
    const spend = container.querySelector('[data-stat="spend"]')!;
    // Cross-node text — assert via textContent (avg is split between
    // " · avg " literal and the formatted USD span).
    // 0.0072 / 4 = 0.0018 → formatUsd renders as "$0.00180".
    expect(spend.textContent).toMatch(/avg \$0\.001\d+\/task/);
  });

  it("renders per-agent breakdown (up to 4 agents)", async () => {
    vi.spyOn(api, "telemetry").mockResolvedValue(SAMPLE);
    const { container } = render(<TelemetryFooter />);
    await waitFor(() =>
      expect(container.querySelector('[data-agent="sysadmin"]')).not.toBeNull(),
    );
    expect(container.querySelector('[data-agent="sysadmin"]')?.textContent).toContain("3×");
    expect(container.querySelector('[data-agent="programmer"]')).not.toBeNull();
  });

  it("skips the average when settled <= 1 (no point in 'avg' with one sample)", async () => {
    vi.spyOn(api, "telemetry").mockResolvedValue({
      ...SAMPLE,
      totals: { ...SAMPLE.totals, settled: 1 },
    });
    render(<TelemetryFooter />);
    await waitFor(() => screen.getByText(/spent/));
    expect(screen.queryByText(/avg/)).toBeNull();
  });
});
