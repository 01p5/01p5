import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { usePolling } from "./usePolling";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

async function flushMicrotasks(): Promise<void> {
  await act(async () => { await Promise.resolve(); });
}

describe("usePolling", () => {
  it("calls fn on mount and stores data", async () => {
    const fn = vi.fn().mockResolvedValue("v1");
    const { result } = renderHook(() => usePolling(fn, 1000));
    await flushMicrotasks();
    expect(fn).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBe("v1");
    expect(result.current.error).toBeNull();
  });

  it("calls fn again after intervalMs", async () => {
    const fn = vi.fn().mockResolvedValue("v");
    renderHook(() => usePolling(fn, 1000));
    await flushMicrotasks();
    expect(fn).toHaveBeenCalledTimes(1);

    await act(async () => { vi.advanceTimersByTime(1000); await Promise.resolve(); });
    expect(fn).toHaveBeenCalledTimes(2);

    await act(async () => { vi.advanceTimersByTime(1000); await Promise.resolve(); });
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("does not poll when pause=true", async () => {
    const fn = vi.fn().mockResolvedValue("x");
    renderHook(() => usePolling(fn, 500, true));
    await flushMicrotasks();
    // With pause=true the effect returns early — fn is never called.
    expect(fn).not.toHaveBeenCalled();

    await act(async () => { vi.advanceTimersByTime(2000); await Promise.resolve(); });
    expect(fn).not.toHaveBeenCalled();
  });

  it("refresh() bumps tick → fn re-runs", async () => {
    const fn = vi.fn().mockResolvedValue("x");
    const { result } = renderHook(() => usePolling(fn, 99999));
    await flushMicrotasks();
    expect(fn).toHaveBeenCalledTimes(1);

    act(() => { result.current.refresh(); });
    await flushMicrotasks();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("captures errors from fn into state", async () => {
    vi.useRealTimers();
    const fn = vi.fn().mockRejectedValue(new Error("blam"));
    const { result } = renderHook(() => usePolling(fn, 1000));
    await waitFor(() => expect(result.current.error?.message).toBe("blam"));
  });

  it("clears the interval on unmount", async () => {
    const fn = vi.fn().mockResolvedValue("x");
    const { unmount } = renderHook(() => usePolling(fn, 1000));
    await flushMicrotasks();
    expect(fn).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => { vi.advanceTimersByTime(5000); await Promise.resolve(); });
    expect(fn).toHaveBeenCalledTimes(1); // no further calls after unmount.
  });
});
