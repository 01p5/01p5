import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HostsPage } from "./HostsPage";
import { api } from "../api";

// Render as an authenticated admin so the admin-only "Sync from Terraform"
// control is present. Other tests don't assert its absence, so this is safe.
vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({
    auth: { state: "authed", email: "a@tianleyu.com", isAdmin: true },
    refresh: vi.fn(),
  }),
}));

afterEach(() => { vi.restoreAllMocks(); cleanup(); });

function renderPage() {
  return render(<MemoryRouter><HostsPage /></MemoryRouter>);
}

describe("HostsPage", () => {
  it("renders hosts + keys from the API", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue([
      {
        id: "h1", name: "cp", address: "10.0.0.1",
        ssh_user: "ubuntu", ssh_port: 22, key_id: "k1",
        groups: ["control_plane"], vars: { region: "us-west-2" },
        description: "", created_at: 0, updated_at: 0,
      },
    ]);
    vi.spyOn(api, "listKeys").mockResolvedValue([
      { id: "k1", name: "prod", fingerprint: "SHA256:abc123def", created_at: 0 },
    ]);
    renderPage();
    expect(await screen.findByText("cp")).toBeInTheDocument();
    expect(screen.getByText("ubuntu@10.0.0.1")).toBeInTheDocument();
    expect(screen.getByText(/control_plane/)).toBeInTheDocument();
    expect(screen.getByText(/region=us-west-2/)).toBeInTheDocument();
    expect(screen.getByText("prod")).toBeInTheDocument();
    expect(screen.getByText("SHA256:abc123def")).toBeInTheDocument();
  });

  it("shows the empty state for hosts and keys", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue([]);
    vi.spyOn(api, "listKeys").mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/no hosts yet/i)).toBeInTheDocument();
    expect(screen.getByText(/no keys yet/i)).toBeInTheDocument();
  });

  it("surfaces API errors", async () => {
    vi.spyOn(api, "listHosts").mockRejectedValue(new Error("net down"));
    vi.spyOn(api, "listKeys").mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/net down/)).toBeInTheDocument();
  });

  it("opens the add-host modal when the button is clicked", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue([]);
    vi.spyOn(api, "listKeys").mockResolvedValue([]);
    renderPage();
    const addBtn = await screen.findByRole("button", { name: /add host/i });
    fireEvent.click(addBtn);
    await waitFor(() =>
      expect(screen.getByText(/name \(alias\)/i)).toBeInTheDocument(),
    );
  });

  it("calls addHost when the modal Create button is pressed", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue([]);
    vi.spyOn(api, "listKeys").mockResolvedValue([]);
    const add = vi.spyOn(api, "addHost").mockResolvedValue({
      id: "x", name: "cp", address: "10.0.0.1",
      ssh_user: "ubuntu", ssh_port: 22, key_id: null,
      groups: [], vars: {}, description: "", created_at: 0, updated_at: 0,
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /add host/i }));
    const inputs = await screen.findAllByRole("textbox");
    // First textbox is name, second is address (per Field order).
    fireEvent.change(inputs[0], { target: { value: "cp" } });
    fireEvent.change(inputs[1], { target: { value: "10.0.0.1" } });
    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => expect(add).toHaveBeenCalled());
    const args = add.mock.calls[0][0];
    expect(args.name).toBe("cp");
    expect(args.address).toBe("10.0.0.1");
  });

  it("syncs hosts from terraform (admin button)", async () => {
    vi.spyOn(api, "listHosts").mockResolvedValue([]);
    vi.spyOn(api, "listKeys").mockResolvedValue([]);
    const sync = vi.spyOn(api, "syncTerraform").mockResolvedValue({
      added: ["demo-host"], skipped: [], errors: [],
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /sync from terraform/i }));
    const dir = await screen.findByPlaceholderText(/applied\/stack/i);
    fireEvent.change(dir, { target: { value: "/tmp/demo-host" } });
    fireEvent.click(screen.getByRole("button", { name: /^sync$/i }));
    await waitFor(() => expect(screen.getByText(/added demo-host/)).toBeInTheDocument());
    expect(sync).toHaveBeenCalledWith("/tmp/demo-host");
  });
});
