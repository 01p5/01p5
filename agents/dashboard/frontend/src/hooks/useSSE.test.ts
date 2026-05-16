import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSSE } from "./useSSE";

class MockEventSource {
  url: string;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  static instances: MockEventSource[] = [];
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close(): void { this.closed = true; }
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useSSE", () => {
  it("constructs EventSource with the given URL", () => {
    renderHook(() => useSSE<{ x: number }>("/events", () => {}));
    expect(MockEventSource.instances.length).toBe(1);
    expect(MockEventSource.instances[0].url).toBe("/events");
  });

  it("does not construct EventSource when url is null", () => {
    renderHook(() => useSSE<{ x: number }>(null, () => {}));
    expect(MockEventSource.instances.length).toBe(0);
  });

  it("parses JSON and forwards to the handler on each onmessage", () => {
    const handler = vi.fn();
    renderHook(() => useSSE<{ x: number }>("/events", handler));
    const src = MockEventSource.instances[0];
    act(() => {
      src.onmessage?.({ data: JSON.stringify({ x: 1 }) } as MessageEvent<string>);
      src.onmessage?.({ data: JSON.stringify({ x: 2 }) } as MessageEvent<string>);
    });
    expect(handler).toHaveBeenCalledTimes(2);
    expect(handler).toHaveBeenNthCalledWith(1, { x: 1 });
    expect(handler).toHaveBeenNthCalledWith(2, { x: 2 });
  });

  it("ignores malformed JSON without calling the handler", () => {
    const handler = vi.fn();
    renderHook(() => useSSE<{ x: number }>("/events", handler));
    const src = MockEventSource.instances[0];
    act(() => {
      src.onmessage?.({ data: "not-json {{" } as MessageEvent<string>);
    });
    expect(handler).not.toHaveBeenCalled();
  });

  it("reconnects after 2s when onerror fires", () => {
    vi.useFakeTimers();
    renderHook(() => useSSE<{ x: number }>("/events", () => {}));
    expect(MockEventSource.instances.length).toBe(1);

    const first = MockEventSource.instances[0];
    act(() => {
      first.onerror?.({} as Event);
    });
    // The current source should be closed; no new source yet (waiting on 2s).
    expect(first.closed).toBe(true);
    expect(MockEventSource.instances.length).toBe(1);

    act(() => { vi.advanceTimersByTime(2000); });
    expect(MockEventSource.instances.length).toBe(2);
    expect(MockEventSource.instances[1].url).toBe("/events");
  });

  it("closes the source on unmount", () => {
    const { unmount } = renderHook(() => useSSE<unknown>("/events", () => {}));
    const src = MockEventSource.instances[0];
    expect(src.closed).toBe(false);
    unmount();
    expect(src.closed).toBe(true);
  });

  it("uses the latest handler without reconnecting on every render", () => {
    const h1 = vi.fn();
    const h2 = vi.fn();
    const { rerender } = renderHook(({ h }: { h: (d: unknown) => void }) => useSSE("/events", h), {
      initialProps: { h: h1 },
    });
    expect(MockEventSource.instances.length).toBe(1);
    const src = MockEventSource.instances[0];

    rerender({ h: h2 });
    // Same EventSource — not torn down + rebuilt.
    expect(MockEventSource.instances.length).toBe(1);
    expect(src.closed).toBe(false);

    act(() => {
      src.onmessage?.({ data: JSON.stringify({ a: 1 }) } as MessageEvent<string>);
    });
    expect(h1).not.toHaveBeenCalled();
    expect(h2).toHaveBeenCalledWith({ a: 1 });
  });
});
