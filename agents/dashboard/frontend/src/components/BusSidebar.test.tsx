import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { BusSidebar } from "./BusSidebar";

// Mocked EventSource — captures the latest instance so tests can push
// messages into it via .onmessage. Mirrors the bits of the spec the
// real BusSidebar relies on: constructor(url), onmessage, onerror, close().
class MockEventSource {
  url: string;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  static last: MockEventSource | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.last = this;
  }
  close(): void {}
}

beforeEach(() => {
  MockEventSource.last = null;
  vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function makeEvent(over: Partial<{
  msg_id: string;
  task_id: string;
  sender: string;
  recipient: string;
  kind: string;
  timestamp: number;
  payload: unknown;
}> = {}): string {
  return JSON.stringify({
    msg_id: over.msg_id ?? `m-${Math.random()}`,
    task_id: over.task_id ?? "t1",
    sender: over.sender ?? "alice",
    recipient: over.recipient ?? "bob",
    kind: over.kind ?? "task",
    timestamp: over.timestamp ?? 1_700_000_000,
    payload: over.payload ?? "hello",
  });
}

describe("BusSidebar", () => {
  it("renders empty placeholder before any events arrive", () => {
    render(<BusSidebar />);
    expect(screen.getByText("no events yet")).toBeInTheDocument();
    expect(MockEventSource.last?.url).toBe("/events");
  });

  it("renders events newest-first as they arrive", () => {
    const { container } = render(<BusSidebar />);
    const src = MockEventSource.last!;

    act(() => {
      src.onmessage?.({
        data: makeEvent({ msg_id: "m1", sender: "alpha", kind: "task" }),
      } as MessageEvent<string>);
    });
    act(() => {
      src.onmessage?.({
        data: makeEvent({ msg_id: "m2", sender: "beta", kind: "result" }),
      } as MessageEvent<string>);
    });

    const rows = container.querySelectorAll(".event");
    expect(rows.length).toBe(2);
    // Newest first: m2 (beta) before m1 (alpha).
    expect(rows[0].textContent).toContain("beta");
    expect(rows[1].textContent).toContain("alpha");
  });

  it("applies kind-tone class to the .kind span", () => {
    const { container } = render(<BusSidebar />);
    const src = MockEventSource.last!;

    act(() => {
      src.onmessage?.({
        data: makeEvent({ kind: "approval_request", msg_id: "ar1" }),
      } as MessageEvent<string>);
    });
    const kindSpan = container.querySelector(".event .kind");
    expect(kindSpan).not.toBeNull();
    expect(kindSpan).toHaveClass("text-accent-yellow");
    expect(kindSpan?.textContent).toBe("approval_request");
  });

  it("silently ignores malformed JSON frames", () => {
    const { container } = render(<BusSidebar />);
    const src = MockEventSource.last!;
    act(() => {
      src.onmessage?.({ data: "not-json {{{" } as MessageEvent<string>);
    });
    expect(container.querySelectorAll(".event").length).toBe(0);
    expect(screen.getByText("no events yet")).toBeInTheDocument();
  });
});
