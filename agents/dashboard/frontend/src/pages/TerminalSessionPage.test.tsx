import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { wsUrlFor, TerminalSessionPage } from "./TerminalSessionPage";


// Capture mock instances so each test can drive ws events.
class MockWebSocket {
  static OPEN = 1;
  static instances: MockWebSocket[] = [];
  url: string;
  readyState = 0;
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  sentFrames: (string | ArrayBuffer | Uint8Array)[] = [];
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string | ArrayBuffer | Uint8Array): void { this.sentFrames.push(data); }
  close(): void { this.closed = true; }
}


beforeEach(() => {
  vi.restoreAllMocks();
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
});
afterEach(() => { vi.unstubAllGlobals(); cleanup(); });


function renderAt(sessionId = "abc123"): void {
  render(
    <MemoryRouter initialEntries={[`/terminal/${sessionId}`]}>
      <Routes>
        <Route path="/terminal/:sessionId" element={<TerminalSessionPage />} />
        <Route path="/terminal" element={<div>sessions-list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}


describe("wsUrlFor", () => {
  it("returns ws:// on http", () => {
    Object.defineProperty(window, "location", {
      value: { protocol: "http:", host: "localhost:5173" },
      writable: true,
    });
    expect(wsUrlFor("abc")).toBe("ws://localhost:5173/terminal/sessions/abc/ws");
  });

  it("returns wss:// on https", () => {
    Object.defineProperty(window, "location", {
      value: { protocol: "https:", host: "0lympu5.com" },
      writable: true,
    });
    expect(wsUrlFor("abc")).toBe("wss://0lympu5.com/terminal/sessions/abc/ws");
  });

  it("percent-encodes weird session ids", () => {
    Object.defineProperty(window, "location", {
      value: { protocol: "http:", host: "x" },
      writable: true,
    });
    expect(wsUrlFor("tk/weird")).toBe("ws://x/terminal/sessions/tk%2Fweird/ws");
  });
});


describe("TerminalSessionPage", () => {
  it("renders the header with session id slice + Back link", () => {
    renderAt("abcdef123456789");
    expect(screen.getByText(/sessions/i)).toBeInTheDocument();
    expect(screen.getByText("abcdef123456")).toBeInTheDocument();
  });

  it("dials a WebSocket at the right path on mount", () => {
    renderAt("session-xyz");
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url)
      .toMatch(/\/terminal\/sessions\/session-xyz\/ws$/);
  });

  it("conn pill flips from connecting → connected on ws.open", async () => {
    renderAt();
    expect(screen.getByTestId("conn-pill").textContent).toMatch(/connecting/i);
    const ws = MockWebSocket.instances[0];
    ws.readyState = MockWebSocket.OPEN;
    ws.onopen?.();
    await waitFor(() =>
      expect(screen.getByTestId("conn-pill").textContent).toMatch(/connected/i),
    );
  });

  it("conn pill flips to closed + overlay shows on ws.close", async () => {
    renderAt();
    const ws = MockWebSocket.instances[0];
    ws.onclose?.({ code: 1006, reason: "abnormal closure" });
    await waitFor(() =>
      expect(screen.getByTestId("conn-pill").textContent).toMatch(/closed/i),
    );
    expect(screen.getByText(/session closed/i)).toBeInTheDocument();
    expect(screen.getByText(/abnormal closure/)).toBeInTheDocument();
  });

  it("conn pill flips to error + overlay shows on ws.error", async () => {
    renderAt();
    const ws = MockWebSocket.instances[0];
    ws.onerror?.(new Event("error"));
    await waitFor(() =>
      expect(screen.getByTestId("conn-pill").textContent).toMatch(/error/i),
    );
    expect(screen.getByText(/connection error/i)).toBeInTheDocument();
  });

  it("closes the WebSocket on unmount", () => {
    const { unmount } = render(
      <MemoryRouter initialEntries={["/terminal/sess"]}>
        <Routes>
          <Route path="/terminal/:sessionId" element={<TerminalSessionPage />} />
        </Routes>
      </MemoryRouter>,
    );
    const ws = MockWebSocket.instances[0];
    expect(ws.closed).toBe(false);
    unmount();
    expect(ws.closed).toBe(true);
  });
});
