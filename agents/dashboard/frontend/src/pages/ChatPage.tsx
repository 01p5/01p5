import { useEffect, useRef, useState } from "react";
import { Send, Bot, User, Sparkles, AlertCircle, ChevronDown, Plus } from "lucide-react";
import clsx from "clsx";
import ReactMarkdown from "react-markdown";
import { api } from "../api";
import { useSSE } from "../hooks/useSSE";
import type { BusEvent, TaskRecord } from "../types";
import { MemoryChips } from "../components/MemoryChips";
import { FeedbackButtons } from "../components/FeedbackButtons";

// exported for tests
export interface Turn {
  task_id: string;
  user: string;
  status: "pending" | "running" | "success" | "failed" | "rejected" | "cancelled";
  agent?: string;
  summary?: string;
  artifacts?: Record<string, unknown>;
  error?: string;
  approvalsPending: number;
  submitted: number;
}

/**
 * Chat — the marquee tab. Submits NL tasks to /tasks, watches the
 * matching task_id stream on /events, renders one user/assistant
 * bubble pair per turn. Fallback polls /tasks/{id} for the final
 * result in case SSE drops a frame.
 */
export function ChatPage(): JSX.Element {
  const [turns, setTurns] = useState<Turn[]>([]);
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Stick to bottom on new content.
  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [turns]);

  // SSE: route events into the matching turn.
  useSSE<BusEvent>("/events", (ev) => {
    setTurns((prev) => {
      const idx = prev.findIndex((t) => t.task_id === ev.task_id);
      if (idx === -1) return prev;
      const t = { ...prev[idx] };
      if (ev.kind === "task" && ev.recipient !== "orchestrator") {
        t.agent = ev.recipient;
        if (t.status === "pending") t.status = "running";
      } else if (ev.kind === "approval_request") {
        t.approvalsPending += 1;
      } else if (ev.kind === "approval_decision") {
        t.approvalsPending = Math.max(0, t.approvalsPending - 1);
      } else if (ev.kind === "result") {
        const p = (ev.payload ?? {}) as Partial<TaskRecord> & { artifacts?: Record<string, unknown> };
        t.status = (p.status as Turn["status"]) ?? "success";
        t.summary = (p as unknown as { summary?: string }).summary ?? "";
        t.artifacts = (p as { artifacts?: Record<string, unknown> }).artifacts ?? undefined;
      }
      const next = [...prev];
      next[idx] = t;
      return next;
    });
  });

  // Fallback poll for any in-flight turn — keeps the UI honest if SSE
  // drops a frame between the agent finishing and us subscribing.
  useEffect(() => {
    const id = setInterval(async () => {
      const pending = turnsRef.current.filter(
        (t) => t.status === "pending" || t.status === "running",
      );
      for (const t of pending) {
        try {
          const r = await api.getTask(t.task_id);
          if (r.status !== "running" && r.status !== "pending") {
            setTurns((prev) => prev.map((p) =>
              p.task_id === t.task_id
                ? {
                    ...p,
                    status: r.status,
                    summary: r.result_summary ?? p.summary,
                    artifacts: r.result_artifacts ?? p.artifacts,
                    error: r.error ?? p.error,
                  }
                : p,
            ));
          }
        } catch { /* ignore polling errors */ }
      }
    }, 3000);
    return () => clearInterval(id);
  }, []);

  // Single submission path used by both the form's onSubmit and the
  // empty-state example buttons. Takes the text directly so the
  // examples don't need to round-trip through React's controlled-input
  // setter (which doesn't fire from a direct DOM mutation).
  //
  // Conversation context: the dashboard backend is stateless per task
  // (each /tasks submission spins up a fresh LangGraph agent with no
  // memory of prior turns). To make the chat feel like a conversation
  // we prepend the last few completed turns' question + summary as
  // history into the prompt — the bubble UI still shows just what the
  // user typed.
  const submit = async (text: string): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    const promptWithContext = buildPromptWithContext(trimmed, turnsRef.current);
    try {
      const { task_id } = await api.submitTask(promptWithContext);
      const turn: Turn = {
        task_id, user: trimmed, status: "pending",
        approvalsPending: 0, submitted: Date.now(),
      };
      setTurns((prev) => [...prev, turn]);
      setInput("");
    } catch (err) {
      setTurns((prev) => [...prev, {
        task_id: `error-${Date.now()}`, user: trimmed, status: "failed",
        error: (err as Error).message, approvalsPending: 0, submitted: Date.now(),
      }]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const resetConversation = (): void => {
    setTurns([]);
    setInput("");
    inputRef.current?.focus();
  };

  const send = (e: React.FormEvent): void => {
    e.preventDefault();
    void submit(input);
  };

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border-subtle flex items-center justify-between bg-dark-secondary/40">
        <div className="flex items-baseline gap-3">
          <Sparkles size={16} className="text-accent-green self-center" strokeWidth={2.25} />
          <h1 className="font-display text-base font-semibold text-text-primary">
            Conversation
          </h1>
          <span className="text-[11px] font-mono text-text-muted">
            agents pick the right tool · gpt-5-mini
            {turns.length > 0 && (
              <> · {turns.filter((t) => t.summary).length} turn(s) of context</>
            )}
          </span>
        </div>
        <button
          onClick={resetConversation}
          disabled={turns.length === 0}
          className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-green border border-border-subtle hover:border-accent-green/40 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          title="Clear the chat history so the next turn starts fresh"
        >
          <Plus size={13} strokeWidth={2.25} />
          New
        </button>
      </div>

      {/* Message stream */}
      <div
        ref={streamRef}
        id="chat-stream"
        className="flex-1 overflow-auto px-6 py-8 space-y-6"
      >
        {turns.length === 0 && <EmptyChat onPick={submit} />}
        {turns.map((t) => <TurnView key={t.task_id} turn={t} />)}
      </div>

      {/* Composer */}
      <form
        id="task-form"
        onSubmit={send}
        className="border-t border-border-subtle bg-dark-secondary/40 px-6 py-4"
      >
        <div className="max-w-4xl mx-auto flex gap-3">
          <input
            ref={inputRef}
            id="task-input"
            autoComplete="off"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Describe a task — e.g. 'list pods in default namespace'"
            className="flex-1 bg-dark-panel border border-border-subtle rounded-md px-4 py-3 text-sm text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60 transition-colors"
          />
          <button
            id="task-submit"
            type="submit"
            disabled={sending}
            className="px-5 py-3 bg-accent-green text-dark-primary font-semibold rounded-md hover:bg-accent-green-dim transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <Send size={18} strokeWidth={2.25} />
            Send
          </button>
        </div>
        <div className="max-w-4xl mx-auto text-[11px] font-mono text-text-muted mt-2 px-1">
          ↵ submits · destructive tools surface an approval card in the right sidebar
        </div>
      </form>
    </section>
  );
}

function EmptyChat({ onPick }: { onPick: (text: string) => void }): JSX.Element {
  const examples = [
    "list pods in default namespace",
    "describe the olympus-olympus pod and show its last 50 log lines",
    "what nodes are in this cluster and how much CPU is being used",
    "generate a Dockerfile for a Python 3.12 service",
  ];
  return (
    <div className="max-w-3xl mx-auto py-10 text-center space-y-6">
      <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-accent-green/10 border border-accent-green/30">
        <Sparkles size={20} className="text-accent-green" />
      </div>
      <div>
        <h2 className="font-display text-2xl font-semibold text-text-primary mb-2">
          Ask Olympus
        </h2>
        <p className="text-sm text-text-secondary">
          Describe an operation in plain English. The right agent picks
          up the request, picks the right tools, and routes destructive
          actions through the approval queue.
        </p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-left">
        {examples.map((ex) => (
          <button
            key={ex}
            onClick={() => onPick(ex)}
            className="text-sm bg-dark-panel border border-border-subtle hover:border-accent-green/40 hover:text-text-primary text-text-secondary rounded-md px-3 py-2.5 transition-colors"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }): JSX.Element {
  // The W7-8 intelligence layer (memory retrieval, feedback, rollback)
  // only makes sense on settled turns — a still-running turn has no
  // memory entry to give feedback on, and the retrieval at submit time
  // is what shaped the agent's reply, so we surface it post-hoc.
  const settled = turn.status === "success"
    || turn.status === "failed"
    || turn.status === "rejected";

  return (
    <div className="space-y-3">
      {/* User bubble — right-aligned */}
      <div className="flex justify-end">
        <div className="max-w-[80%] flex items-start gap-2 flex-row-reverse">
          <div className="flex-shrink-0 w-7 h-7 rounded-full bg-dark-control border border-border-subtle flex items-center justify-center">
            <User size={14} className="text-text-secondary" />
          </div>
          <div className="bg-accent-blue/[0.08] border border-accent-blue/20 rounded-md rounded-tr-sm px-4 py-2.5 text-sm text-text-primary whitespace-pre-wrap break-words">
            {turn.user}
          </div>
        </div>
      </div>
      {/* Assistant bubble — left-aligned */}
      <div className="flex">
        <div className="max-w-[80%] flex items-start gap-2">
          <div className="flex-shrink-0 w-7 h-7 rounded-full bg-accent-green/10 border border-accent-green/30 flex items-center justify-center">
            <Bot size={14} className="text-accent-green" />
          </div>
          <div className="flex-1">
            <AssistantBody turn={turn} />
            {settled && turn.summary && (
              <MemoryChips
                taskId={turn.task_id}
                query={turn.user}
                agent={turn.agent}
              />
            )}
            <div className="text-[10px] font-mono text-text-muted mt-1 pl-1">
              {new Date(turn.submitted).toLocaleTimeString()}
              {" · "}
              <code>{turn.task_id.slice(0, 8)}</code>
              {turn.agent && <> · agent: {turn.agent}</>}
            </div>
            {settled && turn.summary && (
              <FeedbackButtons taskId={turn.task_id} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function AssistantBody({ turn }: { turn: Turn }): JSX.Element {
  if (turn.status === "failed" && turn.error) {
    return (
      <div className="bg-accent-red/10 border border-accent-red/30 rounded-md px-4 py-2.5 text-sm text-accent-red flex items-start gap-2">
        <AlertCircle size={14} className="mt-0.5 flex-shrink-0" />
        <div className="flex-1 font-mono text-xs break-all">{turn.error}</div>
      </div>
    );
  }
  if (turn.status === "pending" || turn.status === "running") {
    return (
      <div className="bg-dark-panel border border-border-subtle rounded-md rounded-tl-sm px-4 py-2.5">
        <RunningIndicator turn={turn} />
      </div>
    );
  }
  // Done
  const failedTone = turn.status !== "success";
  return (
    <div
      className={clsx(
        "rounded-md rounded-tl-sm px-4 py-3",
        failedTone
          ? "bg-accent-red/10 border border-accent-red/30"
          : "bg-dark-panel border border-border-subtle",
      )}
    >
      {turn.summary
        ? <CollapsibleProse text={turn.summary} />
        : <div className="text-text-muted italic text-sm">(no summary)</div>}
      {turn.artifacts && Object.keys(turn.artifacts).length > 0 && (
        <ArtifactsDetails artifacts={turn.artifacts} />
      )}
    </div>
  );
}

function RunningIndicator({ turn }: { turn: Turn }): JSX.Element {
  const label =
    turn.approvalsPending > 0 ? "awaiting your approval — see the right sidebar"
    : turn.agent ? `running on ${turn.agent} agent…`
    : "picking the right agent…";

  return (
    <div className="flex items-center gap-2 text-sm text-text-secondary">
      <span className="inline-flex gap-1">
        <span className="w-1.5 h-1.5 bg-accent-green rounded-full animate-pulse" />
        <span className="w-1.5 h-1.5 bg-accent-green rounded-full animate-pulse" style={{ animationDelay: "0.2s" }} />
        <span className="w-1.5 h-1.5 bg-accent-green rounded-full animate-pulse" style={{ animationDelay: "0.4s" }} />
      </span>
      <span>{label}</span>
    </div>
  );
}

/**
 * Renders an assistant response with a "show more / show less" toggle
 * when it's too long. Keeps short answers inline (no clutter); collapses
 * long ones so a single huge dump of kubectl output doesn't blow the
 * chat box up. Threshold is character count, not lines — handles both
 * "long paragraph" and "code-block wall" responses uniformly.
 */
const COLLAPSE_AT_CHARS = 600;
function CollapsibleProse({ text }: { text: string }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const tooLong = text.length > COLLAPSE_AT_CHARS;
  const visible = expanded || !tooLong ? text : text.slice(0, COLLAPSE_AT_CHARS) + "…";

  return (
    <div className="space-y-2">
      <div
        className={clsx(
          "prose max-w-none",
          // When collapsed, also cap visual height as a second line of defence
          // in case a single line is super wide.
          !expanded && tooLong && "max-h-64 overflow-hidden",
        )}
      >
        <ReactMarkdown>{visible}</ReactMarkdown>
      </div>
      {tooLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-green transition-colors"
        >
          {expanded
            ? "show less ↑"
            : `show more ↓ (${text.length - COLLAPSE_AT_CHARS} more chars)`}
        </button>
      )}
    </div>
  );
}

function ArtifactsDetails({ artifacts }: { artifacts: Record<string, unknown> }): JSX.Element {
  return (
    <details className="mt-3 border-t border-border-subtle/60 pt-2">
      <summary className="text-[11px] font-mono text-text-muted cursor-pointer hover:text-text-secondary flex items-center gap-1">
        <ChevronDown size={10} />
        artifacts
      </summary>
      <pre className="mt-2 text-[12px] font-mono text-text-secondary bg-dark-primary border border-border-subtle rounded p-2 overflow-auto max-h-80">
        {JSON.stringify(artifacts, null, 2)}
      </pre>
    </details>
  );
}

/**
 * Build a prompt that includes recent completed turns as conversation
 * history. Each turn becomes a "User: …" / "Assistant: …" pair; only
 * turns with a settled status + summary contribute (so an in-flight
 * "shut them down" doesn't poison the next turn with empty context).
 *
 * Window is the last MAX_HISTORY_TURNS to keep token cost bounded.
 * The system prompt is unchanged; this just augments the user message
 * so the dashboard backend stays per-task-stateless.
 */
const MAX_HISTORY_TURNS = 6;
// exported for tests
export function buildPromptWithContext(userText: string, turns: Turn[]): string {
  const completed = turns.filter(
    (t) => t.summary && (t.status === "success" || t.status === "rejected"),
  );
  if (completed.length === 0) return userText;

  const window = completed.slice(-MAX_HISTORY_TURNS);
  const history = window
    .map((t) => `User: ${t.user}\nAssistant: ${t.summary}`)
    .join("\n\n");
  return [
    "You are continuing an ongoing conversation. Use the prior turns",
    "below to resolve references like 'them', 'both', 'that pod', etc.",
    "When the user refers to entities from earlier turns, act on those.",
    "",
    "---- prior conversation ----",
    history,
    "---- end prior conversation ----",
    "",
    `Current user message: ${userText}`,
  ].join("\n");
}
