import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
});
