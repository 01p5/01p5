import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProgrammerPage } from "./ProgrammerPage";
import { api } from "../api";

beforeEach(() => {
  vi.restoreAllMocks();
  // happy-dom doesn't ship a global alert(); the page calls it after save.
  window.alert = vi.fn();
});
afterEach(() => {
  cleanup();
});

describe("ProgrammerPage — Dockerfile generator", () => {
  it("click Generate → POSTs generate_dockerfile, then save → write_file with path+content", async () => {
    const invokeSpy = vi.spyOn(api, "invokeTool").mockImplementation(
      async (_agent: string, tool: string) => ({
        task_id: "t",
        agent: "programmer",
        tool,
        result: "FROM python:3.13\n",
      }),
    );

    render(<ProgrammerPage />);
    // Locate the Dockerfile card by its header.
    expect(screen.getByText("Dockerfile")).toBeInTheDocument();

    // Change language → node, version → 20, cmd → "node server.js".
    // Find the language select via label "language" (it appears multiple times,
    // so scope by select element with options for python/node/go).
    const selects = screen.getAllByRole("combobox");
    const langSelect = selects.find((s) =>
      Array.from((s as HTMLSelectElement).options).some((o) => o.value === "node"),
    ) as HTMLSelectElement;
    expect(langSelect).toBeDefined();
    await userEvent.selectOptions(langSelect, "node");

    const versionField = screen.getByDisplayValue("3.12");
    await userEvent.clear(versionField);
    await userEvent.type(versionField, "20");

    const cmdField = screen.getByDisplayValue("python app.py");
    await userEvent.clear(cmdField);
    await userEvent.type(cmdField, "node server.js");

    const generateButtons = screen.getAllByRole("button", { name: /^generate$/i });
    await act(async () => { await userEvent.click(generateButtons[0]); });

    expect(invokeSpy).toHaveBeenCalledWith("programmer", "generate_dockerfile", {
      language: "node",
      version: "20",
      cmd: ["node", "server.js"],
    });
    // Result panel shows.
    await waitFor(() => expect(screen.getAllByText(/FROM python:3.13/)[0]).toBeInTheDocument());

    // Save button appears now that out !== null. Multiple cards have a save
    // button — find the one in the Dockerfile card (first one).
    window.confirm = vi.fn().mockReturnValue(true);
    const saveButtons = screen.getAllByRole("button", { name: /save \(gated\)/i });
    await act(async () => { await userEvent.click(saveButtons[0]); });

    expect(invokeSpy).toHaveBeenCalledWith("programmer", "write_file", {
      path: "/tmp/Dockerfile",
      content: "FROM python:3.13\n",
    });
  });
});

describe("ProgrammerPage — Compose generator", () => {
  it("click Generate → POSTs generate_compose_service with name, image, port", async () => {
    const invokeSpy = vi.spyOn(api, "invokeTool").mockImplementation(
      async (_agent: string, tool: string) => ({
        task_id: "t", agent: "programmer", tool, result: "services:\n  web: {}",
      }),
    );

    render(<ProgrammerPage />);
    expect(screen.getByText("docker-compose service")).toBeInTheDocument();

    // The compose generator's Generate is the 2nd one on the page.
    const generateButtons = screen.getAllByRole("button", { name: /^generate$/i });
    await act(async () => { await userEvent.click(generateButtons[1]); });

    expect(invokeSpy).toHaveBeenCalledWith("programmer", "generate_compose_service", {
      name: "web",
      image: "nginx:alpine",
      port: 8080,
    });
    await waitFor(() => expect(screen.getAllByText(/services:/)[0]).toBeInTheDocument());
  });

  it("save → write_file with compose path + content", async () => {
    vi.spyOn(api, "invokeTool").mockImplementation(
      async (_agent: string, tool: string) => ({
        task_id: "t", agent: "programmer", tool, result: "compose-yaml",
      }),
    );

    render(<ProgrammerPage />);
    const generateButtons = screen.getAllByRole("button", { name: /^generate$/i });
    await act(async () => { await userEvent.click(generateButtons[1]); });
    await waitFor(() => screen.getAllByText("compose-yaml"));

    window.confirm = vi.fn().mockReturnValue(true);
    const saveButtons = screen.getAllByRole("button", { name: /save \(gated\)/i });
    await act(async () => { await userEvent.click(saveButtons[0]); });

    expect(api.invokeTool).toHaveBeenCalledWith("programmer", "write_file", {
      path: "/tmp/docker-compose.yml",
      content: "compose-yaml",
    });
  });
});

describe("ProgrammerPage — Helm generator", () => {
  it("click Generate → POSTs generate_helm_values with service_name + image + port + replicas", async () => {
    const invokeSpy = vi.spyOn(api, "invokeTool").mockImplementation(
      async (_agent: string, tool: string) => ({
        task_id: "t", agent: "programmer", tool, result: "image: olympus/dashboard\n",
      }),
    );

    render(<ProgrammerPage />);
    expect(screen.getByText(/helm values\.yaml/i)).toBeInTheDocument();

    const generateButtons = screen.getAllByRole("button", { name: /^generate$/i });
    await act(async () => { await userEvent.click(generateButtons[2]); });

    expect(invokeSpy).toHaveBeenCalledWith("programmer", "generate_helm_values", {
      service_name: "olympus-app",
      image: "olympus/dashboard",
      port: 8765,
      replicas: 1,
    });
    await waitFor(() => expect(screen.getAllByText(/image:/)[0]).toBeInTheDocument());
  });

  it("save → write_file with helm path + content", async () => {
    vi.spyOn(api, "invokeTool").mockImplementation(
      async (_agent: string, tool: string) => ({
        task_id: "t", agent: "programmer", tool, result: "helm-yaml",
      }),
    );

    render(<ProgrammerPage />);
    const generateButtons = screen.getAllByRole("button", { name: /^generate$/i });
    await act(async () => { await userEvent.click(generateButtons[2]); });
    await waitFor(() => screen.getAllByText("helm-yaml"));

    window.confirm = vi.fn().mockReturnValue(true);
    const saveButtons = screen.getAllByRole("button", { name: /save \(gated\)/i });
    await act(async () => { await userEvent.click(saveButtons[0]); });

    expect(api.invokeTool).toHaveBeenCalledWith("programmer", "write_file", {
      path: "/tmp/values.yaml",
      content: "helm-yaml",
    });
  });
});
