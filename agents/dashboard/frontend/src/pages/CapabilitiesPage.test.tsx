import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, Navigate, useLocation } from "react-router-dom";
import { CapabilitiesPage } from "./CapabilitiesPage";

afterEach(() => { cleanup(); });

let lastLocation = "";
function LocationCapture(): null {
  const loc = useLocation();
  lastLocation = loc.pathname;
  return null;
}

function renderAt(initial = "/capabilities"): void {
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="capabilities" element={<CapabilitiesPage />}>
          <Route index element={<Navigate to="kubernetes" replace />} />
          <Route path="kubernetes" element={<div>k8s-stub</div>} />
          <Route path="terraform"  element={<div>tf-stub</div>} />
          <Route path="ansible"    element={<div>ansible-stub</div>} />
          <Route path="programmer" element={<div>prog-stub</div>} />
        </Route>
        <Route path="kubernetes" element={<Navigate to="/capabilities/kubernetes" replace />} />
        <Route path="terraform"  element={<Navigate to="/capabilities/terraform"  replace />} />
        <Route path="ansible"    element={<Navigate to="/capabilities/ansible"    replace />} />
        <Route path="programmer" element={<Navigate to="/capabilities/programmer" replace />} />
      </Routes>
      <LocationCapture />
    </MemoryRouter>,
  );
}

describe("CapabilitiesPage", () => {
  it("renders header + all four sub-tabs", () => {
    renderAt("/capabilities/kubernetes");
    expect(screen.getByRole("heading", { name: /capabilities/i })).toBeInTheDocument();
    const nav = screen.getByTestId("capabilities-subnav");
    ["Kubernetes", "Terraform", "Ansible", "Programmer"].forEach((label) => {
      expect(nav.querySelector(`a[href$="${label.toLowerCase()}"]`)).not.toBeNull();
    });
  });

  it("slurm + gpu are new-tab links (not iframed routes)", () => {
    renderAt("/capabilities/kubernetes");
    const nav = screen.getByTestId("capabilities-subnav");
    for (const [testid, href] of [["portal-link-slurm", "/slurm/"], ["portal-link-gpu", "/gpu/"]]) {
      const link = nav.querySelector(`[data-testid="${testid}"]`) as HTMLAnchorElement;
      expect(link).not.toBeNull();
      expect(link.getAttribute("href")).toBe(href);
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
    }
  });

  it("redirects /capabilities (no sub-tab) to kubernetes", () => {
    renderAt("/capabilities");
    expect(lastLocation).toBe("/capabilities/kubernetes");
    expect(screen.getByText("k8s-stub")).toBeInTheDocument();
  });

  it("marks the matching sub-tab as active via aria-current", () => {
    renderAt("/capabilities/terraform");
    const active = screen.getByRole("link", { name: /terraform/i });
    expect(active).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /^kubernetes$/i })).not.toHaveAttribute("aria-current");
  });

  it("clicking a sub-tab swaps the outlet content", async () => {
    renderAt("/capabilities/kubernetes");
    expect(screen.getByText("k8s-stub")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: /ansible/i }));
    expect(screen.getByText("ansible-stub")).toBeInTheDocument();
    expect(screen.queryByText("k8s-stub")).toBeNull();
    expect(lastLocation).toBe("/capabilities/ansible");
  });

  it.each([
    ["/kubernetes", "/capabilities/kubernetes"],
    ["/terraform",  "/capabilities/terraform"],
    ["/ansible",    "/capabilities/ansible"],
    ["/programmer", "/capabilities/programmer"],
  ])("legacy top-level route %s redirects to %s", (legacy, target) => {
    renderAt(legacy);
    expect(lastLocation).toBe(target);
  });
});
