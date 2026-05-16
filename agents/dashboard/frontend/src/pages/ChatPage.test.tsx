import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act, fireEvent, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatPage, buildPromptWithContext, type Turn } from "./ChatPage";
import { api } from "../api";

// EventSource stub shared by all tests; tests can grab .latest to push.
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

beforeEach(() => {
  vi.restoreAllMocks();
  MockEventSource.latest = null;
  vi.stubGlobal("EventSource", MockEventSource as unknown as typeof EventSource);
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("ChatPage — empty state", () => {
  it("renders the EmptyChat heading and 4 example buttons", () => {
    render(<ChatPage />);
    expect(screen.getByRole("heading", { name: /ask olympus/i })).toBeInTheDocument();
    const exampleButtons = screen
      .getAllByRole("button")
      .filter((b) => b.textContent && b.textContent.length > 20 && !b.textContent.includes("Send"));
    expect(exampleButtons.length).toBeGreaterThanOrEqual(4);
  });

  it("New button is disabled when chat is empty", () => {
    render(<ChatPage />);
    const newBtn = screen.getByRole("button", { name: /^New$/i });
    expect(newBtn).toBeDisabled();
  });
});

describe("ChatPage — submission", () => {
  it("submitting via form clears input and creates user + assistant pending bubbles", async () => {
    vi.spyOn(api, "submitTask").mockResolvedValue({ task_id: "t-1" });
    render(<ChatPage />);

    const input = screen.getByPlaceholderText(/describe a task/i) as HTMLInputElement;
    await userEvent.type(input, "list pods");
    expect(input.value).toBe("list pods");

    await act(async () => {
      fireEvent.submit(input.closest("form")!);
    });

    expect(api.submitTask).toHaveBeenCalled();
    // User bubble shows the text.
    expect(screen.getByText("list pods")).toBeInTheDocument();
    // Assistant bubble shows the pending status indicator.
    expect(screen.getByText(/picking the right agent/i)).toBeInTheDocument();
    // Input cleared after submit.
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("clicking an example button submits directly (no extra typing)", async () => {
    vi.spyOn(api, "submitTask").mockResolvedValue({ task_id: "t-2" });
    render(<ChatPage />);
    const exBtn = screen.getByText(/list pods in default namespace/i);
    await act(async () => {
      await userEvent.click(exBtn);
    });
    expect(api.submitTask).toHaveBeenCalledWith(
      expect.stringContaining("list pods in default namespace"),
    );
  });

  it("New button clears turns and re-enables disabled state", async () => {
    vi.spyOn(api, "submitTask").mockResolvedValue({ task_id: "t-3" });
    render(<ChatPage />);
    const input = screen.getByPlaceholderText(/describe a task/i) as HTMLInputElement;
    await userEvent.type(input, "hi");
    await act(async () => { fireEvent.submit(input.closest("form")!); });

    await waitFor(() => screen.getByText("hi"));
    const newBtn = screen.getByRole("button", { name: /^New$/i });
    expect(newBtn).not.toBeDisabled();

    await userEvent.click(newBtn);
    await waitFor(() => expect(screen.queryByText("hi")).not.toBeInTheDocument());
    expect(newBtn).toBeDisabled();
  });
});

describe("ChatPage — SSE result event updates bubble", () => {
  it("a kind:'result' event for a tracked task updates the bubble content", async () => {
    vi.spyOn(api, "submitTask").mockResolvedValue({ task_id: "task-A" });
    render(<ChatPage />);

    const input = screen.getByPlaceholderText(/describe a task/i) as HTMLInputElement;
    await userEvent.type(input, "do thing");
    await act(async () => { fireEvent.submit(input.closest("form")!); });

    await waitFor(() => screen.getByText(/picking the right agent/i));

    const src = MockEventSource.latest!;
    await act(async () => {
      src.onmessage?.({
        data: JSON.stringify({
          msg_id: "m1",
          task_id: "task-A",
          sender: "agent",
          recipient: "orchestrator",
          kind: "result",
          timestamp: 1,
          payload: { status: "success", summary: "Did the thing." },
        }),
      } as MessageEvent<string>);
    });

    // The pending "picking the right agent…" status should be gone.
    expect(screen.queryByText(/picking the right agent/i)).not.toBeInTheDocument();
    // The summary should be rendered (markdown rendered, but plain text shows as-is).
    expect(screen.getByText(/did the thing/i)).toBeInTheDocument();
  });
});

describe("buildPromptWithContext", () => {
  const mkTurn = (over: Partial<Turn>): Turn => ({
    task_id: "x",
    user: "u",
    status: "success",
    summary: "s",
    approvalsPending: 0,
    submitted: 0,
    ...over,
  });

  it("returns the plain user text when there is no history", () => {
    expect(buildPromptWithContext("hi", [])).toBe("hi");
  });

  it("filters out failed / pending / running turns and turns without summary", () => {
    const turns: Turn[] = [
      mkTurn({ task_id: "a", user: "U1", summary: "S1", status: "success" }),
      mkTurn({ task_id: "b", user: "U2", summary: undefined, status: "pending" }),
      mkTurn({ task_id: "c", user: "U3", summary: "S3", status: "failed" }),
      mkTurn({ task_id: "d", user: "U4", summary: "S4", status: "running" }),
    ];
    const out = buildPromptWithContext("now", turns);
    expect(out).toContain("U1");
    expect(out).toContain("S1");
    expect(out).not.toContain("U2");
    expect(out).not.toContain("U3");
    expect(out).not.toContain("U4");
    expect(out).toContain("now");
  });

  it("includes all 6 prior completed turns when count <= 6", () => {
    const turns: Turn[] = Array.from({ length: 6 }, (_, i) =>
      mkTurn({ task_id: `t${i}`, user: `U${i}`, summary: `S${i}`, status: "success" }),
    );
    const out = buildPromptWithContext("ask", turns);
    for (let i = 0; i < 6; i++) {
      expect(out).toContain(`U${i}`);
      expect(out).toContain(`S${i}`);
    }
    // Order: U0 before U1 before … before U5.
    expect(out.indexOf("U0")).toBeLessThan(out.indexOf("U5"));
  });

  it("includes only the last 6 of 8 prior completed turns", () => {
    const turns: Turn[] = Array.from({ length: 8 }, (_, i) =>
      mkTurn({ task_id: `t${i}`, user: `U${i}`, summary: `S${i}`, status: "success" }),
    );
    const out = buildPromptWithContext("ask", turns);
    expect(out).not.toContain("U0");
    expect(out).not.toContain("U1");
    for (let i = 2; i < 8; i++) {
      expect(out).toContain(`U${i}`);
    }
  });

  it("includes rejected turns (with summary) as history", () => {
    const turns: Turn[] = [
      mkTurn({ task_id: "a", user: "U1", summary: "S1", status: "rejected" }),
    ];
    const out = buildPromptWithContext("ask", turns);
    expect(out).toContain("U1");
    expect(out).toContain("S1");
  });
});

describe("CollapsibleProse via ChatPage", () => {
  // Test by sending an SSE result with short vs long summary text.
  async function setupWithSummary(summary: string): Promise<void> {
    vi.spyOn(api, "submitTask").mockResolvedValue({ task_id: "task-C" });
    render(<ChatPage />);
    const input = screen.getByPlaceholderText(/describe a task/i) as HTMLInputElement;
    await userEvent.type(input, "q");
    await act(async () => { fireEvent.submit(input.closest("form")!); });
    await waitFor(() => screen.getByText(/picking the right agent/i));

    const src = MockEventSource.latest!;
    await act(async () => {
      src.onmessage?.({
        data: JSON.stringify({
          msg_id: "m1",
          task_id: "task-C",
          sender: "agent",
          recipient: "orchestrator",
          kind: "result",
          timestamp: 1,
          payload: { status: "success", summary },
        }),
      } as MessageEvent<string>);
    });
  }

  it("short text (<600 chars) renders without a show-more toggle", async () => {
    await setupWithSummary("a short reply");
    expect(screen.getByText("a short reply")).toBeInTheDocument();
    expect(screen.queryByText(/show more/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/show less/i)).not.toBeInTheDocument();
  });

  it("long text (>600 chars) shows a 'show more' button that expands", async () => {
    const longText = "x".repeat(700);
    await setupWithSummary(longText);
    const more = await screen.findByText(/show more/i);
    expect(more).toBeInTheDocument();
    await userEvent.click(more);
    expect(await screen.findByText(/show less/i)).toBeInTheDocument();
  });
});
