import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TerraformPage } from "./TerraformPage";
import { api } from "../api";

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  cleanup();
});

const STACKS = ["terraform", "terraform/aws", "terraform/pve"];

describe("TerraformPage", () => {
  it("renders one card per stack from terraformStacks()", async () => {
    vi.spyOn(api, "terraformStacks").mockResolvedValue(STACKS);
    const { container } = render(<TerraformPage />);
    await waitFor(() => {
      const headings = container.querySelectorAll("h3");
      expect(headings.length).toBe(3);
    });
    const headings = Array.from(container.querySelectorAll("h3")).map((h) => h.textContent);
    expect(headings).toEqual(expect.arrayContaining(STACKS));
  });

  it("clicking plan → POSTs tf_plan, opens modal with 'Apply this plan' header action", async () => {
    vi.spyOn(api, "terraformStacks").mockResolvedValue(STACKS);
    const invokeSpy = vi
      .spyOn(api, "invokeTool")
      .mockResolvedValue({ task_id: "t", agent: "terraform", tool: "tf_plan", result: "Plan: 1 to add." });

    const { container } = render(<TerraformPage />);
    await waitFor(() => {
      expect(container.querySelectorAll("h3").length).toBe(3);
    });

    const planButtons = screen.getAllByRole("button", { name: /^plan$/i });
    await act(async () => {
      await userEvent.click(planButtons[0]);
    });

    expect(invokeSpy).toHaveBeenCalledWith("terraform", "tf_plan", {
      working_dir: "/opt/olympus/infra/terraform",
    });
    // Modal opens; header has 'Apply this plan'.
    await waitFor(() => expect(screen.getByRole("button", { name: /apply this plan/i })).toBeInTheDocument());
    // Plan output renders in code block.
    expect(screen.getByText(/Plan: 1 to add/)).toBeInTheDocument();
  });

  it("clicking destroy + confirm=true → POSTs tf_destroy", async () => {
    vi.spyOn(api, "terraformStacks").mockResolvedValue(STACKS);
    const invokeSpy = vi
      .spyOn(api, "invokeTool")
      .mockResolvedValue({ task_id: "t", agent: "terraform", tool: "tf_destroy", result: "" });
    window.confirm = vi.fn().mockReturnValue(true);

    const { container } = render(<TerraformPage />);
    await waitFor(() => {
      expect(container.querySelectorAll("h3").length).toBe(3);
    });

    const destroyButtons = screen.getAllByRole("button", { name: /destroy/i });
    await act(async () => {
      await userEvent.click(destroyButtons[0]);
    });

    expect(invokeSpy).toHaveBeenCalledWith("terraform", "tf_destroy", {
      working_dir: "/opt/olympus/infra/terraform",
    });
  });

  it("clicking destroy + confirm=false → no tf_destroy POST", async () => {
    vi.spyOn(api, "terraformStacks").mockResolvedValue(STACKS);
    const invokeSpy = vi.spyOn(api, "invokeTool").mockResolvedValue({
      task_id: "t", agent: "terraform", tool: "tf_destroy", result: "",
    });
    window.confirm = vi.fn().mockReturnValue(false);

    const { container } = render(<TerraformPage />);
    await waitFor(() => {
      expect(container.querySelectorAll("h3").length).toBe(3);
    });

    const destroyButtons = screen.getAllByRole("button", { name: /destroy/i });
    await act(async () => {
      await userEvent.click(destroyButtons[0]);
    });

    const destroyCall = invokeSpy.mock.calls.find((c) => c[1] === "tf_destroy");
    expect(destroyCall).toBeUndefined();
  });

  it("shows error card when terraformStacks() rejects", async () => {
    vi.spyOn(api, "terraformStacks").mockRejectedValue(new Error("nope"));
    render(<TerraformPage />);
    await waitFor(() => expect(screen.getByText(/nope/i)).toBeInTheDocument());
  });

  it("shows empty-stacks message when no stacks", async () => {
    vi.spyOn(api, "terraformStacks").mockResolvedValue([]);
    render(<TerraformPage />);
    await waitFor(() => expect(screen.getByText(/no terraform stacks found/i)).toBeInTheDocument());
  });
});
