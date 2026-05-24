import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnsiblePage } from "./AnsiblePage";
import { api } from "../api";

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  cleanup();
});

const PBS = ["ansible/site.yml", "ansible/bootstrap.yml"];
const DEFAULT_INV = "/opt/olympus/infra/terraform/deployment/inventory.ini";

describe("AnsiblePage", () => {
  it("renders one card per playbook + default inventory value", async () => {
    vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue(PBS);
    render(<AnsiblePage />);

    await waitFor(() => expect(screen.getByText("site.yml")).toBeInTheDocument());
    expect(screen.getByText("bootstrap.yml")).toBeInTheDocument();

    // inventory field should have the default path.
    const invField = screen.getByDisplayValue(DEFAULT_INV);
    expect(invField).toBeInTheDocument();
  });

  it("clicking 'check' → POSTs ansible/check_playbook with playbook + inventory", async () => {
    vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue(PBS);
    const invokeSpy = vi.spyOn(api, "invokeTool").mockResolvedValue({
      task_id: "t",
      agent: "ansible",
      tool: "check_playbook",
      result: "PLAY [all] *** ok",
    });

    render(<AnsiblePage />);
    await waitFor(() => screen.getByText("site.yml"));

    const checkButtons = screen.getAllByRole("button", { name: /check/i });
    await act(async () => {
      await userEvent.click(checkButtons[0]);
    });

    expect(invokeSpy).toHaveBeenCalledWith("ansible", "check_playbook", {
      playbook: "/opt/olympus/infra/ansible/site.yml",
      inventory: DEFAULT_INV,
    });
    // Modal opens with output.
    await waitFor(() => expect(screen.getAllByText(/PLAY \[all\]/).length).toBeGreaterThan(0));
  });

  it("clicking 'run' + confirm=true → POSTs ansible/run_playbook", async () => {
    vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue(PBS);
    const invokeSpy = vi.spyOn(api, "invokeTool").mockResolvedValue({
      task_id: "t", agent: "ansible", tool: "run_playbook", result: "",
    });
    window.confirm = vi.fn().mockReturnValue(true);

    render(<AnsiblePage />);
    await waitFor(() => screen.getByText("site.yml"));

    const runButtons = screen.getAllByRole("button", { name: /^run$/i });
    await act(async () => {
      await userEvent.click(runButtons[0]);
    });

    expect(window.confirm).toHaveBeenCalled();
    expect(invokeSpy).toHaveBeenCalledWith("ansible", "run_playbook", {
      playbook: "/opt/olympus/infra/ansible/site.yml",
      inventory: DEFAULT_INV,
    });
  });

  it("clicking 'run' + confirm=false → no run_playbook POST", async () => {
    vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue(PBS);
    const invokeSpy = vi.spyOn(api, "invokeTool").mockResolvedValue({
      task_id: "t", agent: "ansible", tool: "run_playbook", result: "",
    });
    window.confirm = vi.fn().mockReturnValue(false);

    render(<AnsiblePage />);
    await waitFor(() => screen.getByText("site.yml"));

    const runButtons = screen.getAllByRole("button", { name: /^run$/i });
    await act(async () => {
      await userEvent.click(runButtons[0]);
    });

    const runCall = invokeSpy.mock.calls.find((c) => c[1] === "run_playbook");
    expect(runCall).toBeUndefined();
  });

  it("clicking 'list inventory' → POSTs ansible/list_inventory", async () => {
    vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue(PBS);
    const invokeSpy = vi.spyOn(api, "invokeTool").mockResolvedValue({
      task_id: "t", agent: "ansible", tool: "list_inventory", result: "master ansible_host=...",
    });

    render(<AnsiblePage />);
    await waitFor(() => screen.getByText("site.yml"));

    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /list inventory/i }));
    });

    expect(invokeSpy).toHaveBeenCalledWith("ansible", "list_inventory", {
      inventory: DEFAULT_INV,
    });
    await waitFor(() => expect(screen.getAllByText(/master ansible_host/i).length).toBeGreaterThan(0));
  });

  it("list inventory failure → 'Inventory error' modal", async () => {
    vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue(PBS);
    vi.spyOn(api, "invokeTool").mockRejectedValue(new Error("no inv"));

    render(<AnsiblePage />);
    await waitFor(() => screen.getByText("site.yml"));
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /list inventory/i }));
    });
    await waitFor(() => expect(screen.getByText(/no inv/)).toBeInTheDocument());
  });

  it("check failure → 'check failed' modal with rejection message", async () => {
    vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue(PBS);
    vi.spyOn(api, "invokeTool").mockRejectedValue(new Error("syntax: bad indent"));

    render(<AnsiblePage />);
    await waitFor(() => screen.getByText("site.yml"));

    const checkButtons = screen.getAllByRole("button", { name: /check/i });
    await act(async () => { await userEvent.click(checkButtons[0]); });

    await waitFor(() => expect(screen.getByText(/syntax: bad indent/)).toBeInTheDocument());
  });

  it("includes 'limit' when user fills the field", async () => {
    vi.spyOn(api, "ansiblePlaybooks").mockResolvedValue(PBS);
    const invokeSpy = vi.spyOn(api, "invokeTool").mockResolvedValue({
      task_id: "t", agent: "ansible", tool: "check_playbook", result: "",
    });

    render(<AnsiblePage />);
    await waitFor(() => screen.getByText("site.yml"));

    const limitField = screen.getByPlaceholderText(/all \/ master/i);
    await userEvent.type(limitField, "workers");

    const checkButtons = screen.getAllByRole("button", { name: /check/i });
    await act(async () => {
      await userEvent.click(checkButtons[0]);
    });

    expect(invokeSpy).toHaveBeenCalledWith("ansible", "check_playbook", {
      playbook: "/opt/olympus/infra/ansible/site.yml",
      inventory: DEFAULT_INV,
      limit: "workers",
    });
  });
});
