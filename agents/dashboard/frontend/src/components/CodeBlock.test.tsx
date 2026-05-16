import { describe, it, expect, vi } from "vitest";
import { render, screen, act, fireEvent, waitFor } from "@testing-library/react";
import { CodeBlock } from "./CodeBlock";

describe("CodeBlock", () => {
  it("renders a plain <code> for non-diff languages", () => {
    const { container } = render(<CodeBlock text="hello world" language="yaml" />);
    // For non-diff, the text is in a single <code> with no internal split.
    const code = container.querySelector("code");
    expect(code).not.toBeNull();
    expect(code!.textContent).toBe("hello world");
    // No tinted line spans expected.
    expect(code!.querySelectorAll("span.text-accent-green").length).toBe(0);
  });

  it("renders DiffHighlight when language=diff (green +, red -, blue @@)", () => {
    const diff = [
      "--- a/foo",
      "+++ b/foo",
      "@@ -1,1 +1,1 @@",
      "-old line",
      "+new line",
      " unchanged",
    ].join("\n");
    const { container } = render(<CodeBlock text={diff} language="diff" />);
    expect(container.querySelector("span.text-accent-green")).not.toBeNull();
    expect(container.querySelector("span.text-accent-red")).not.toBeNull();
    expect(container.querySelector("span.text-accent-blue")).not.toBeNull();
    // Header lines (+++, ---) get text-muted, not green/red.
    const muted = container.querySelectorAll("span.text-text-muted");
    expect(muted.length).toBeGreaterThanOrEqual(2);
  });

  it("shows language label when language is provided", () => {
    render(<CodeBlock text="x" language="hcl" />);
    expect(screen.getByText("hcl")).toBeInTheDocument();
  });

  it("calls navigator.clipboard.writeText on copy and flips to check icon", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const original = Object.getOwnPropertyDescriptor(globalThis.navigator, "clipboard");
    Object.defineProperty(globalThis.navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    const { container } = render(<CodeBlock text="payload" />);
    const copyBtn = container.querySelector("button") as HTMLButtonElement;
    expect(copyBtn).not.toBeNull();

    // Click — copy() is async (calls clipboard.writeText then setCopied).
    // Wrap in act + flush microtasks so state updates land.
    await act(async () => {
      fireEvent.click(copyBtn);
    });

    expect(writeText).toHaveBeenCalledWith("payload");
    expect(copyBtn.getAttribute("title")).toBe("copied");

    // Reverts to "copy" after the 1.5s setTimeout. waitFor polls.
    await waitFor(
      () => expect(copyBtn.getAttribute("title")).toBe("copy"),
      { timeout: 2000 },
    );

    if (original) Object.defineProperty(globalThis.navigator, "clipboard", original);
  });

  it("renders (empty) placeholder when text is empty", () => {
    render(<CodeBlock text="" />);
    expect(screen.getByText("(empty)")).toBeInTheDocument();
  });
});
