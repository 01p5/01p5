import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SubpathPortalPage } from "./SubpathPortalPage";


function renderPage() {
  return render(
    <MemoryRouter>
      <SubpathPortalPage
        path="/slurm/"
        title="Slurm dashboard"
        enableHint="slurmDashboard.enabled=true"
        testIdPrefix="slurm-portal"
      />
    </MemoryRouter>,
  );
}


describe("SubpathPortalPage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the iframe when /healthz returns 200", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true } as Response);
    renderPage();
    await waitFor(() => {
      const iframe = screen.getByTestId("slurm-portal-iframe");
      expect(iframe).toBeInTheDocument();
      expect(iframe.getAttribute("src")).toBe("/slurm/");
    });
  });

  it("shows the nudge when /healthz returns non-200", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: false } as Response);
    renderPage();
    await waitFor(() => {
      const nudge = screen.getByTestId("slurm-portal-down");
      expect(nudge).toBeInTheDocument();
      expect(nudge.textContent).toContain("slurmDashboard.enabled=true");
    });
    expect(screen.queryByTestId("slurm-portal-iframe")).not.toBeInTheDocument();
  });

  it("shows the nudge when /healthz throws (network error)", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("ECONNREFUSED"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("slurm-portal-down")).toBeInTheDocument();
    });
  });

  it("shows a loading state before /healthz resolves", () => {
    // Never-resolving fetch — page should sit on the "loading" state.
    (fetch as unknown as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId("slurm-portal-loading")).toBeInTheDocument();
  });
});
