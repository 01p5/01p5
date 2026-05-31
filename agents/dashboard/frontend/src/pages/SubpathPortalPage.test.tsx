import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { SubpathPortalPage } from "./SubpathPortalPage";


function renderPage(initialEntry = "/capabilities/slurm") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/capabilities/slurm/*"
          element={
            <SubpathPortalPage
              path="/slurm/"
              title="Slurm dashboard"
              enableHint="slurmDashboard.enabled=true"
              testIdPrefix="slurm-portal"
              parentRoute="/capabilities/slurm"
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}


// Helper that renders the page + exposes the current router location
// so tests can assert that postMessage triggered a parent navigate.
function renderPageWithLocationProbe(initialEntry = "/capabilities/slurm") {
  const probe = { pathname: "" };
  function Probe() {
    const loc = useLocation();
    probe.pathname = loc.pathname;
    return null;
  }
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path="/capabilities/slurm/*"
          element={
            <>
              <Probe />
              <SubpathPortalPage
                path="/slurm/"
                title="Slurm dashboard"
                enableHint="slurmDashboard.enabled=true"
                testIdPrefix="slurm-portal"
                parentRoute="/capabilities/slurm"
              />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return probe;
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

  it("S2.F3: iframe src honors the wildcard inner path on mount", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true } as Response);
    renderPage("/capabilities/slurm/jobs");
    await waitFor(() => {
      const iframe = screen.getByTestId("slurm-portal-iframe");
      // Note the inner path is appended without a leading slash — the
      // base path's trailing slash supplies the separator.
      expect(iframe.getAttribute("src")).toBe("/slurm/jobs");
    });
  });

  it("S2.F3: postMessage 'embedded-nav' updates parent URL", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true } as Response);
    const probe = renderPageWithLocationProbe("/capabilities/slurm");
    await waitFor(() => {
      expect(screen.getByTestId("slurm-portal-iframe")).toBeInTheDocument();
    });
    expect(probe.pathname).toBe("/capabilities/slurm");
    // Fire the message the embedded SPA would post on click "Jobs".
    await act(async () => {
      window.postMessage(
        { type: "embedded-nav", path: "/slurm/jobs" },
        window.location.origin,
      );
      // postMessage is async — wait a microtask so the listener fires.
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(probe.pathname).toBe("/capabilities/slurm/jobs");
  });

  it("S2.F3: postMessage from foreign origin is ignored", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true } as Response);
    const probe = renderPageWithLocationProbe("/capabilities/slurm");
    await waitFor(() => {
      expect(screen.getByTestId("slurm-portal-iframe")).toBeInTheDocument();
    });
    // postMessage with explicit targetOrigin can't lie, but a same-origin
    // listener also fires for non-embedded-nav payloads — those should
    // be dropped too. Different-type events: no navigate.
    await act(async () => {
      window.postMessage({ type: "something-else", path: "/slurm/jobs" }, window.location.origin);
      window.postMessage(null, window.location.origin);
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(probe.pathname).toBe("/capabilities/slurm");
  });
});
