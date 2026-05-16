import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CostChip, formatUsd, formatSeconds } from "./CostChip";

describe("formatUsd", () => {
  it.each([
    [0, "$0"],
    [0.0001, "$0.00010"],
    [0.00012, "$0.00012"],
    [0.0123, "$0.0123"],
    [1.234, "$1.2340"],
  ])("formats %s as %s", (input, expected) => {
    expect(formatUsd(input)).toBe(expected);
  });

  it("falls back to exponential notation below 0.0001", () => {
    expect(formatUsd(0.00001)).toMatch(/e/);
  });
});

describe("formatSeconds", () => {
  it.each([
    [0, "0.0s"],
    [1.234, "1.2s"],
    [59.7, "59.7s"],
    [60, "1m0s"],
    [125, "2m5s"],
    [3601, "60m1s"],
  ])("formats %s seconds as %s", (input, expected) => {
    expect(formatSeconds(input)).toBe(expected);
  });
});

describe("CostChip", () => {
  it("renders nothing when both cost and wall time are missing", () => {
    const { container } = render(<CostChip />);
    expect(container.querySelector(".cost-chip")).toBeNull();
  });

  it("renders nothing when both fields are zero/null", () => {
    const { container } = render(<CostChip costUsd={0} wallSeconds={0} />);
    expect(container.querySelector(".cost-chip")).toBeNull();
  });

  it("shows the dollar amount when costUsd > 0", () => {
    const { container } = render(<CostChip costUsd={0.00123} />);
    const chip = container.querySelector(".cost-chip");
    expect(chip).not.toBeNull();
    expect(chip!.textContent).toContain("$0.00123");
  });

  it("shows the wall-time when wallSeconds > 0", () => {
    render(<CostChip wallSeconds={2.5} />);
    expect(screen.getByText(/2\.5s/)).toBeInTheDocument();
  });

  it("includes both fields when both are set", () => {
    render(<CostChip costUsd={0.0012} wallSeconds={3.5} />);
    expect(screen.getByText(/\$0\.00120/)).toBeInTheDocument();
    expect(screen.getByText(/3\.5s/)).toBeInTheDocument();
  });

  it("token suffix appears in the title attribute when tokens are set", () => {
    const { container } = render(
      <CostChip costUsd={0.001} wallSeconds={1} inputTokens={120} outputTokens={80} />,
    );
    const chip = container.querySelector(".cost-chip");
    expect(chip?.getAttribute("title")).toContain("120 in / 80 out");
  });

  it("data attributes expose raw numbers for testability", () => {
    const { container } = render(<CostChip costUsd={0.0012} wallSeconds={2.5} />);
    const chip = container.querySelector(".cost-chip");
    expect(chip?.getAttribute("data-cost-usd")).toBe("0.0012");
    expect(chip?.getAttribute("data-wall-seconds")).toBe("2.5");
  });
});
