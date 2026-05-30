import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ChatPage, actorAccent, payloadText, CollapsibleProse } from "./ChatPage";
import type { TicketEventDTO } from "../types";
import { api } from "../api";

// AUD.5b: ChatPage now uses useNavigate() to sync the first-send
// ticket id into the URL, which requires a Router context in tests.
// MemoryRouter's history is in-memory (window.location doesn't move),
// so we capture the location via a sibling component for assertions.
let lastLocation = "";
function LocationCapture(): null {
  const loc = useLocation();
  lastLocation = loc.pathname;
  return null;
}
function renderChat(initialTicketId?: string): void {
  render(
    <MemoryRouter initialEntries={[initialTicketId ? `/chat/${initialTicketId}` : "/chat"]}>
      <ChatPage initialTicketId={initialTicketId} />
      <LocationCapture />
    </MemoryRouter>,
  );
}

// EventSource stub shared by all tests; tests grab .latest to push events.
class MockEventSource {
  url: string;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  static latest: MockEventSource | null = null;
  constructor(url: string) {
    this.url = url;
    MockEventSource.latest = this;
  }
  close(): void {}
}

let seq = 0;
function mkEvent(over: Partial<TicketEventDTO>): TicketEventDTO {
  seq += 1;
  return {
    ticket_id: "T",
    actor: "main",
    kind: "agent_message",
    payload: {},
    seq,
    event_id: `e${seq}`,
    ts: seq,
    ...over,
  };
}

async function push(ev: TicketEventDTO): Promise<void> {
  const src = MockEventSource.latest!;
  await act(async () => {
    src.onmessage?.({ data: JSON.stringify(ev) } as MessageEvent<string>);
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  MockEventSource.latest = null;
  seq = 0;
  vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("ChatPage — empty state", () => {
  it("renders the EmptyChat heading and example buttons", () => {
    renderChat();
    expect(screen.getByRole("heading", { name: /ask olympus/i })).toBeInTheDocument();
    const examples = screen
      .getAllByRole("button")
      .filter((b) => b.textContent && b.textContent.length > 20 && !b.textContent.includes("Send"));
    expect(examples.length).toBeGreaterThanOrEqual(4);
  });

  it("New button is disabled when the transcript is empty", () => {
    renderChat();
    expect(screen.getByRole("button", { name: /^New$/i })).toBeDisabled();
  });
});

describe("ChatPage — submission", () => {
  it("submitting posts to the ticket and clears the input", async () => {
    const spy = vi.spyOn(api, "sendTicketMessage").mockResolvedValue({ ticket_id: "T" });
    renderChat();
    const input = screen.getByPlaceholderText(/describe a task/i) as HTMLInputElement;
    await userEvent.type(input, "list pods");
    await act(async () => { fireEvent.submit(input.closest("form")!); });

    expect(spy).toHaveBeenCalledWith(expect.any(String), "list pods");
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("clicking an example button posts that text", async () => {
    const spy = vi.spyOn(api, "sendTicketMessage").mockResolvedValue({ ticket_id: "T" });
    renderChat();
    await act(async () => {
      await userEvent.click(screen.getByText(/list pods in default namespace/i));
    });
    expect(spy).toHaveBeenCalledWith(expect.any(String), "list pods in default namespace");
  });

  it("a failed send surfaces a local error message", async () => {
    vi.spyOn(api, "sendTicketMessage").mockRejectedValue(new Error("boom"));
    renderChat();
    const input = screen.getByPlaceholderText(/describe a task/i) as HTMLInputElement;
    await userEvent.type(input, "x");
    await act(async () => { fireEvent.submit(input.closest("form")!); });
    expect(await screen.findByText(/send failed: boom/i)).toBeInTheDocument();
  });

  it("Resolve button closes the ticket and starts a fresh one", async () => {
    vi.spyOn(api, "sendTicketMessage").mockResolvedValue({ ticket_id: "T" });
    const close = vi.spyOn(api, "closeTicket").mockResolvedValue({ ticket_id: "T", summary: "done" });
    renderChat();
    await push(mkEvent({ kind: "human_message", actor: "human", payload: { text: "hello" } }));

    const resolve = screen.getByRole("button", { name: /resolve/i });
    expect(resolve).not.toBeDisabled();
    const firstUrl = MockEventSource.latest!.url;
    await act(async () => { await userEvent.click(resolve); });

    expect(close).toHaveBeenCalledWith(expect.any(String));
    await waitFor(() => expect(screen.queryByText("hello")).not.toBeInTheDocument());
    expect(MockEventSource.latest!.url).not.toBe(firstUrl);
  });

  it("Resolve button is disabled on an empty transcript", () => {
    renderChat();
    expect(screen.getByRole("button", { name: /resolve/i })).toBeDisabled();
  });

  it("New button resets the ticket (subscribes to a new stream)", async () => {
    vi.spyOn(api, "sendTicketMessage").mockResolvedValue({ ticket_id: "T" });
    renderChat();
    await push(mkEvent({ kind: "human_message", actor: "human", payload: { text: "hello" } }));
    expect(screen.getByText("hello")).toBeInTheDocument();

    const firstUrl = MockEventSource.latest!.url;
    await userEvent.click(screen.getByRole("button", { name: /^New$/i }));
    await waitFor(() => expect(screen.queryByText("hello")).not.toBeInTheDocument());
    // A new ticket id => a new SSE url.
    expect(MockEventSource.latest!.url).not.toBe(firstUrl);
  });
});

describe("ChatPage — transcript rendering", () => {
  it("renders a human message bubble", async () => {
    renderChat();
    await push(mkEvent({ kind: "human_message", actor: "human", payload: { text: "hi team" } }));
    expect(screen.getByText("hi team")).toBeInTheDocument();
  });

  it("renders a main agent reply", async () => {
    renderChat();
    await push(mkEvent({ kind: "agent_message", actor: "main", payload: { text: "on it" } }));
    expect(screen.getByText("on it")).toBeInTheDocument();
    expect(screen.getByText(/^Main$/)).toBeInTheDocument();
  });

  it("renders a dispatch chip and a specialist result", async () => {
    renderChat();
    await push(mkEvent({ kind: "dispatch", actor: "main", payload: { to: "sysadmin", subtask: "list pods" } }));
    await push(mkEvent({ kind: "agent_result", actor: "sysadmin", payload: { summary: "3 pods running", status: "success" } }));
    expect(screen.getByText("list pods")).toBeInTheDocument();
    expect(screen.getByText("3 pods running")).toBeInTheDocument();
    // "sysadmin" appears in both the dispatch chip and the result bubble.
    expect(screen.getAllByText("sysadmin").length).toBeGreaterThanOrEqual(1);
  });

  it("renders a specialist result's artifacts (data) in a collapsible block", async () => {
    renderChat();
    await push(mkEvent({
      kind: "agent_result",
      actor: "sysadmin",
      payload: {
        summary: "Listed pods.",
        status: "success",
        artifacts: { findings: { pods: "nginx-test Running" } },
      },
    }));
    expect(screen.getByText("Listed pods.")).toBeInTheDocument();
    // The data toggle is present, and the actual pod data is in the DOM.
    expect(screen.getByText("data")).toBeInTheDocument();
    expect(screen.getByText(/nginx-test Running/)).toBeInTheDocument();
  });

  it("renders an ask_agent question line", async () => {
    renderChat();
    await push(mkEvent({ kind: "agent_message", actor: "programmer", payload: { type: "question", to: "sysadmin", question: "is the pod up?" } }));
    expect(screen.getByText("is the pod up?")).toBeInTheDocument();
    expect(screen.getByText("asks")).toBeInTheDocument();
  });

  it("renders a tool_call line with the tool name", async () => {
    renderChat();
    await push(mkEvent({ kind: "tool_call", actor: "sysadmin", payload: { tool: "kubectl_get", args: { kind: "pods" }, result: "ok", approved: true } }));
    expect(screen.getByText("kubectl_get")).toBeInTheDocument();
    expect(screen.getByText("approved")).toBeInTheDocument();
  });

  it("renders an approval-request notice", async () => {
    renderChat();
    await push(mkEvent({ kind: "approval_request", actor: "sysadmin", payload: { tool: "delete_pod" } }));
    expect(screen.getByText(/requested approval for delete_pod/i)).toBeInTheDocument();
  });

  it("dedupes events by event_id (no double render on replay)", async () => {
    renderChat();
    const ev = mkEvent({ kind: "human_message", actor: "human", payload: { text: "once" } });
    await push(ev);
    await push(ev); // same event_id replayed
    expect(screen.getAllByText("once")).toHaveLength(1);
  });

  it("syncs the ticket id into the URL after the first send (AUD.5b)", async () => {
    vi.spyOn(api, "sendTicketMessage").mockResolvedValue({ ticket_id: "ignored" });
    // Inline the route under a Routes so we can read the location.
    // Easier: render the page and assert window.location after submit.
    renderChat();
    // First send: triggers POST then navigate('/chat/{id}', { replace: true }).
    const input = await screen.findByPlaceholderText(/describe a task/i);
    await userEvent.type(input, "hello world");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(api.sendTicketMessage).toHaveBeenCalled());
    // sendTicketMessage's first arg is the generated ticket id — it
    // should match the URL after the post-submit navigate fires.
    const ticketId = vi.mocked(api.sendTicketMessage).mock.calls[0][0];
    await waitFor(() => {
      expect(lastLocation).toBe(`/chat/${ticketId}`);
    });
  });

  it("does NOT re-navigate on subsequent sends (URL sync is one-shot)", async () => {
    vi.spyOn(api, "sendTicketMessage").mockResolvedValue({ ticket_id: "x" });
    renderChat("tk-existing");
    const input = await screen.findByPlaceholderText(/describe a task/i);
    await userEvent.type(input, "first");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(api.sendTicketMessage).toHaveBeenCalledTimes(1));
    // URL already matched the initial id, no navigate fired.
    expect(lastLocation).toBe("/chat/tk-existing");
    // Second send doesn't perturb the URL either.
    await userEvent.type(input, "second");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(api.sendTicketMessage).toHaveBeenCalledTimes(2));
    expect(lastLocation).toBe("/chat/tk-existing");
  });
});

describe("actorAccent", () => {
  it("gives a distinct label for known actors and falls back for unknown", () => {
    expect(actorAccent("human").label).toBe("You");
    expect(actorAccent("main").text).toContain("green");
    expect(actorAccent("sysadmin").text).toContain("blue");
    expect(actorAccent("programmer").text).toContain("yellow");
    expect(actorAccent("terraform").text).toContain("orange");
    expect(actorAccent("ansible").text).toContain("red");
    expect(actorAccent("hpc").text).toContain("green-dim");
    expect(actorAccent("mystery").label).toBe("mystery");
  });
});

describe("payloadText", () => {
  it("extracts text by kind", () => {
    expect(payloadText(mkEvent({ payload: { text: "t" } }))).toBe("t");
    expect(payloadText(mkEvent({ payload: { answer: "a" } }))).toBe("a");
    expect(payloadText(mkEvent({ payload: { question: "q" } }))).toBe("q");
    expect(payloadText(mkEvent({ payload: { summary: "s" } }))).toBe("s");
    expect(payloadText(mkEvent({ payload: { subtask: "sub" } }))).toBe("sub");
    expect(payloadText(mkEvent({ payload: {} }))).toBe("");
    expect(payloadText(mkEvent({ payload: null }))).toBe("");
  });
});

describe("CollapsibleProse", () => {
  it("short text renders without a toggle", () => {
    render(<CollapsibleProse text="a short reply" />);
    expect(screen.getByText("a short reply")).toBeInTheDocument();
    expect(screen.queryByText(/show more/i)).not.toBeInTheDocument();
  });

  it("long text shows a toggle that expands", async () => {
    render(<CollapsibleProse text={"x".repeat(700)} />);
    const more = await screen.findByText(/show more/i);
    await userEvent.click(more);
    expect(await screen.findByText(/show less/i)).toBeInTheDocument();
  });

  it("renders a GFM markdown table as an actual <table> (remark-gfm)", () => {
    const md = [
      "| Pod | Status |",
      "|---|---|",
      "| nginx | Running |",
    ].join("\n");
    render(<CollapsibleProse text={md} />);
    // Without remark-gfm this stays literal text and there is no table.
    expect(document.querySelector("table")).not.toBeNull();
    expect(screen.getByRole("cell", { name: "nginx" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Pod" })).toBeInTheDocument();
  });
});
