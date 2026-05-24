import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RefreshCw } from "lucide-react";
import { Button } from "./Button";

describe("Button", () => {
  it("renders children", () => {
    render(<Button>Click me</Button>);
    expect(screen.getByRole("button", { name: "Click me" })).toBeInTheDocument();
  });

  it.each([
    ["primary", "bg-accent-green"],
    ["secondary", "bg-dark-control"],
    ["danger", "text-accent-red"],
    ["ghost", "bg-transparent"],
  ] as const)("applies %s variant classes", (variant, expectedClass) => {
    render(<Button variant={variant}>v</Button>);
    expect(screen.getByRole("button")).toHaveClass(expectedClass);
  });

  it.each([
    ["sm", "text-xs"],
    ["md", "text-sm"],
  ] as const)("applies size %s class", (size, expectedClass) => {
    render(<Button size={size}>s</Button>);
    expect(screen.getByRole("button")).toHaveClass(expectedClass);
  });

  it("shows a spinner and disables when loading=true", () => {
    render(<Button loading>Working</Button>);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    // Lucide's Loader2 renders an <svg> with the animate-spin class.
    const spinner = btn.querySelector("svg.animate-spin");
    expect(spinner).not.toBeNull();
  });

  it("renders icon before the text", () => {
    render(
      <Button icon={<span data-testid="ico">I</span>}>Label</Button>,
    );
    const btn = screen.getByRole("button");
    const ico = screen.getByTestId("ico");
    // Icon is the first child of the <button>, text after.
    expect(btn.firstChild).toBe(ico);
    expect(btn.textContent).toContain("Label");
  });

  it("respects the disabled prop", () => {
    render(<Button disabled>Nope</Button>);
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("fires onClick when enabled", async () => {
    const handler = vi.fn();
    render(<Button onClick={handler}>Go</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not fire onClick when disabled", async () => {
    const handler = vi.fn();
    render(<Button disabled onClick={handler}>No</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(handler).not.toHaveBeenCalled();
  });

  it("does not fire onClick when loading", async () => {
    const handler = vi.fn();
    render(<Button loading onClick={handler}>Wait</Button>);
    await userEvent.click(screen.getByRole("button"));
    expect(handler).not.toHaveBeenCalled();
  });

  // Icon normalization — Button is the single source of truth for icon
  // metrics. A lucide icon at 12px effectively vanishes on dark
  // backgrounds, so Button overrides whatever size/strokeWidth the
  // caller passed.
  it("normalizes icon size + strokeWidth for sm buttons (16/2.25)", () => {
    render(<Button size="sm" icon={<RefreshCw size={9} strokeWidth={1} />}>r</Button>);
    const svg = screen.getByRole("button").querySelector("svg");
    expect(svg?.getAttribute("width")).toBe("16");
    expect(svg?.getAttribute("height")).toBe("16");
    expect(svg?.getAttribute("stroke-width")).toBe("2.25");
  });

  it("normalizes icon size + strokeWidth for md buttons (18/2.25)", () => {
    render(<Button size="md" icon={<RefreshCw size={9} strokeWidth={1} />}>r</Button>);
    const svg = screen.getByRole("button").querySelector("svg");
    expect(svg?.getAttribute("width")).toBe("18");
    expect(svg?.getAttribute("height")).toBe("18");
    expect(svg?.getAttribute("stroke-width")).toBe("2.25");
  });

  it("loading spinner inherits the normalized icon size for the button", () => {
    render(<Button size="sm" loading>w</Button>);
    const svg = screen.getByRole("button").querySelector("svg.animate-spin");
    expect(svg?.getAttribute("width")).toBe("16");
  });

  it("leaves a non-lucide icon node untouched (no React element → no clone)", () => {
    render(<Button icon={"★"}>star</Button>);
    expect(screen.getByRole("button").textContent).toContain("★");
  });
});
