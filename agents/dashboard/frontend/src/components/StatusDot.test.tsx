import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { StatusDot } from "./StatusDot";
import { api } from "../api";

describe("StatusDot", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows 'connecting' immediately and 'connected' after health resolves ok", async () => {
    vi.spyOn(api, "health").mockResolvedValue({ ok: true });
    render(<StatusDot />);
    // First render is sync, before promise resolves.
    expect(screen.getByText("connecting")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("connected")).toBeInTheDocument());
  });

  it("shows 'degraded' when health resolves with ok=false", async () => {
    vi.spyOn(api, "health").mockResolvedValue({ ok: false });
    render(<StatusDot />);
    await waitFor(() => expect(screen.getByText("degraded")).toBeInTheDocument());
  });

  it("shows 'offline' when health throws", async () => {
    vi.spyOn(api, "health").mockRejectedValue(new Error("boom"));
    render(<StatusDot />);
    await waitFor(() => expect(screen.getByText("offline")).toBeInTheDocument());
  });

  it("re-polls every 5s", async () => {
    const calls: number[] = [];
    let n = 0;
    const spy = vi.spyOn(api, "health").mockImplementation(async () => {
      calls.push(++n);
      return { ok: true };
    });
    vi.useFakeTimers();
    render(<StatusDot />);
    // Flush the initial async call.
    await act(async () => { await Promise.resolve(); });
    expect(spy).toHaveBeenCalledTimes(1);

    // Advance 5s → second poll.
    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); });
    expect(spy).toHaveBeenCalledTimes(2);

    // Advance another 5s → third.
    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); });
    expect(spy).toHaveBeenCalledTimes(3);
  });
});
