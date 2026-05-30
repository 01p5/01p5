import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { wsUrlFor, TerminalSessionPage } from "./TerminalSessionPage";
import { api } from "../api";


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
  // TERM.8: TerminalSessionsRail is mounted inside the page now; mock
  // the APIs it polls so tests don't fall through to real fetches.
  vi.spyOn(api, "listTerminalSessions").mockResolvedValue([]);
  vi.spyOn(api, "listHosts").mockResolvedValue([]);
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
    // The header's "Sessions" back-link is a router <Link>; scoping by
    // role disambiguates it from the rail's "no live sessions" copy
    // (TERM.8 made that more crowded).
    expect(screen.getByRole("link", { name: /sessions/i })).toBeInTheDocument();
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

  it("writes ArrayBuffer messages to the terminal", () => {
    renderAt();
    const ws = MockWebSocket.instances[0];
    // Should not throw — the branch covers ev.data instanceof ArrayBuffer.
    expect(() => ws.onmessage?.({
      data: new TextEncoder().encode("hi").buffer,
    } as MessageEvent)).not.toThrow();
  });

  it("writes string messages to the terminal (fallback branch)", () => {
    renderAt();
    const ws = MockWebSocket.instances[0];
    // Covers the ``typeof ev.data === 'string'`` fallback branch.
    expect(() => ws.onmessage?.({ data: "hello\n" } as MessageEvent)).not.toThrow();
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

  it("renders the companion panel by default", () => {
    renderAt();
    expect(screen.getByTestId("companion-panel")).toBeInTheDocument();
    expect(screen.getByText(/ask anything about this terminal session/i))
      .toBeInTheDocument();
  });

  it("hides the companion panel when the close button is clicked", async () => {
    renderAt();
    expect(screen.getByTestId("companion-panel")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("companion-close"));
    expect(screen.queryByTestId("companion-panel")).toBeNull();
    expect(screen.getByTestId("companion-open")).toBeInTheDocument();
  });

  it("submitting a question calls askTerminalCompanion + renders answer", async () => {
    const ask = vi.spyOn(api, "askTerminalCompanion").mockResolvedValue({
      answer: "the prompt is in /opt/olympus",
      suggested_commands: ["pwd", "ls"],
    });
    renderAt("sess-1");
    const input = screen.getByTestId("companion-input");
    await userEvent.type(input, "where am I");
    await userEvent.click(screen.getByTestId("companion-send"));
    await waitFor(() => expect(ask).toHaveBeenCalled());
    expect(ask.mock.calls[0][0]).toBe("sess-1");
    expect(ask.mock.calls[0][1]).toBe("where am I");
    expect(await screen.findByText(/the prompt is in/)).toBeInTheDocument();
    expect(screen.getByText("pwd")).toBeInTheDocument();
    expect(screen.getByText("ls")).toBeInTheDocument();
  });

  it("surfaces an error in the history when ask fails", async () => {
    vi.spyOn(api, "askTerminalCompanion").mockRejectedValue(new Error("LLM down"));
    renderAt("sess-1");
    await userEvent.type(screen.getByTestId("companion-input"), "anything");
    await userEvent.click(screen.getByTestId("companion-send"));
    expect(await screen.findByText(/LLM down/)).toBeInTheDocument();
  });

  it("clicking a suggested command sends it through the WebSocket (TERM.5)", async () => {
    vi.spyOn(api, "askTerminalCompanion").mockResolvedValue({
      answer: "looks like the disk filled up",
      suggested_commands: ["df -h", "ncdu /"],
    });
    renderAt("sess-1");
    // Move WS to OPEN so the inject path actually sends.
    const ws = MockWebSocket.instances[0];
    ws.readyState = MockWebSocket.OPEN;
    ws.onopen?.();

    // Ask the companion so suggestions render.
    await userEvent.type(screen.getByTestId("companion-input"), "what's up");
    await userEvent.click(screen.getByTestId("companion-send"));
    await waitFor(() => screen.getByTestId("companion-inject-df -h"));

    // Click the first suggestion → WS receives the command bytes.
    const sentBefore = ws.sentFrames.length;
    await userEvent.click(screen.getByTestId("companion-inject-df -h"));

    // One new BINARY frame was sent containing the command text. No
    // trailing newline — the operator presses Enter themselves.
    expect(ws.sentFrames.length).toBe(sentBefore + 1);
    const lastFrame = ws.sentFrames[ws.sentFrames.length - 1];
    const text = lastFrame instanceof Uint8Array
      ? new TextDecoder().decode(lastFrame)
      : String(lastFrame);
    expect(text).toBe("df -h");
  });

  it("inject is a no-op when the WebSocket isn't OPEN", async () => {
    vi.spyOn(api, "askTerminalCompanion").mockResolvedValue({
      answer: "x", suggested_commands: ["ls"],
    });
    renderAt("sess-1");
    // Leave WS in CONNECTING state.
    await userEvent.type(screen.getByTestId("companion-input"), "?");
    await userEvent.click(screen.getByTestId("companion-send"));
    await waitFor(() => screen.getByTestId("companion-inject-ls"));

    const ws = MockWebSocket.instances[0];
    const beforeFrames = ws.sentFrames.length;
    await userEvent.click(screen.getByTestId("companion-inject-ls"));
    // Nothing sent — guard kicks in.
    expect(ws.sentFrames.length).toBe(beforeFrames);
  });

  it("sends an initial resize JSON frame on ws.open (TERM.6)", () => {
    renderAt();
    const ws = MockWebSocket.instances[0];
    ws.readyState = MockWebSocket.OPEN;
    ws.onopen?.();

    const resizeFrames = ws.sentFrames
      .map((f) => typeof f === "string" ? f : "")
      .filter((s) => s.includes('"type":"resize"'));
    expect(resizeFrames.length).toBeGreaterThanOrEqual(1);
    const parsed = JSON.parse(resizeFrames[0]);
    expect(parsed.type).toBe("resize");
    expect(typeof parsed.cols).toBe("number");
    expect(typeof parsed.rows).toBe("number");
    expect(parsed.cols).toBeGreaterThan(0);
    expect(parsed.rows).toBeGreaterThan(0);
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
