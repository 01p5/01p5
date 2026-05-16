import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "./Modal";

afterEach(() => cleanup());

describe("Modal", () => {
  it("renders nothing when open=false", () => {
    const { container } = render(
      <Modal open={false} onClose={() => {}} title="Hidden">
        <div>inside</div>
      </Modal>,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByText("inside")).not.toBeInTheDocument();
  });

  it("portals into document.body when open=true", () => {
    const { container } = render(
      <Modal open onClose={() => {}} title="Visible">
        <div>inside</div>
      </Modal>,
    );
    // The portal target is document.body — the local container should
    // not contain the modal content directly.
    expect(container.contains(screen.getByText("inside"))).toBe(false);
    expect(document.body).toContainElement(screen.getByText("inside"));
    expect(screen.getByText("Visible")).toBeInTheDocument();
  });

  it("calls onClose when Escape key is pressed", async () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="K">
        <div>inside</div>
      </Modal>,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose on backdrop click but not inside-content click", async () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="C">
        <div data-testid="inside">inside</div>
      </Modal>,
    );

    // Click inside — should NOT close.
    await userEvent.click(screen.getByTestId("inside"));
    expect(onClose).not.toHaveBeenCalled();

    // The backdrop is the outermost fixed div with z-50. It's a sibling
    // wrapper — find it via querySelector since it has no role/text.
    const backdrop = document.querySelector(".fixed.inset-0.z-50") as HTMLElement;
    expect(backdrop).not.toBeNull();
    await userEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders headerAction in the header", () => {
    render(
      <Modal
        open
        onClose={() => {}}
        title="With action"
        headerAction={<button data-testid="ha">Apply</button>}
      >
        body
      </Modal>,
    );
    expect(screen.getByTestId("ha")).toBeInTheDocument();
  });

  it("close-icon button has aria-label", () => {
    render(
      <Modal open onClose={() => {}} title="X">
        <div>x</div>
      </Modal>,
    );
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });
});
