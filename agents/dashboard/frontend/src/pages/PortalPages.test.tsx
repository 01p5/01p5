import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SlurmPortalPage } from "./SlurmPortalPage";
import { GpuPortalPage } from "./GpuPortalPage";

/**
 * Smoke tests for the two thin wrappers around SubpathPortalPage. They
 * just pin path/title/parentRoute, but were sitting at 0% coverage —
 * a render each exercises them (and confirms the right portal path).
 */
describe("portal wrappers", () => {
  beforeEach(() => {
    // healthz never resolves → wrapper sits in the loading state; enough
    // to mount the component + assert which portal it is.
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("SlurmPortalPage mounts + probes /slurm/healthz", async () => {
    render(<MemoryRouter><SlurmPortalPage /></MemoryRouter>);
    expect(screen.getByTestId("slurm-portal-loading")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/slurm/healthz", { method: "GET" }));
  });

  it("GpuPortalPage mounts + probes /gpu/healthz", async () => {
    render(<MemoryRouter><GpuPortalPage /></MemoryRouter>);
    expect(screen.getByTestId("gpu-portal-loading")).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/gpu/healthz", { method: "GET" }));
  });
});
