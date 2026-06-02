import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Send, Bot, User, Sparkles, AlertCircle, Plus, ArrowRight, Wrench, CheckCircle2, ShieldAlert, X, ChevronDown } from "lucide-react";
import clsx from "clsx";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SessionsRail } from "../components/SessionsRail";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import { useSSE } from "../hooks/useSSE";
import type { PendingApproval, TicketEventDTO } from "../types";

/**
 * Chat — the group-chat ticket. The chat session IS a ticket: the human,
 * the generalist main agent, and any specialist sub-agents are
 * participants in one thread. Messages POST to /tickets/{id}/messages
 * (which runs the main agent); the whole transcript — human + agent
 * messages, dispatches, tool calls, ask_agent exchanges — streams back
 * over /tickets/{id}/events. Context is retained server-side per ticket,
 * so there's no client-side history stitching.
 */
function newTicketId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `tk-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// Per-actor display label + accent classes. Distinct colors so the human
// can tell participants apart at a glance in the group chat.
export function actorAccent(actor: string): { label: string; ring: string; text: string } {
  switch (actor) {
    case "human": return { label: "You", ring: "bg-dark-control border-border-subtle", text: "text-text-secondary" };
    case "main": return { label: "Main", ring: "bg-accent-green/10 border-accent-green/30", text: "text-accent-green" };
    case "sysadmin": return { label: "sysadmin", ring: "bg-accent-blue/10 border-accent-blue/30", text: "text-accent-blue" };
    case "programmer": return { label: "programmer", ring: "bg-accent-yellow/10 border-accent-yellow/30", text: "text-accent-yellow" };
    case "terraform": return { label: "terraform", ring: "bg-accent-orange/10 border-accent-orange/30", text: "text-accent-orange" };
    case "ansible": return { label: "ansible", ring: "bg-accent-red/10 border-accent-red/30", text: "text-accent-red" };
    case "hpc": return { label: "hpc", ring: "bg-accent-green-dim/10 border-accent-green-dim/30", text: "text-accent-green-dim" };
    default: return { label: actor, ring: "bg-dark-panel border-border-subtle", text: "text-text-secondary" };
  }
}

// Best-effort text extraction from an event's payload, by kind.
export function payloadText(ev: TicketEventDTO): string {
  const p = (ev.payload ?? {}) as Record<string, unknown>;
  if (typeof p.text === "string") return p.text;
  if (typeof p.answer === "string") return p.answer;
  if (typeof p.question === "string") return p.question;
  if (typeof p.summary === "string") return p.summary;
  if (typeof p.subtask === "string") return p.subtask;
  return "";
}

export function ChatPage({ initialTicketId }: { initialTicketId?: string } = {}): JSX.Element {
  const navigate = useNavigate();
  const [ticketId, setTicketId] = useState<string>(() => initialTicketId || newTicketId());
  // Track whether this ticket's id is already reflected in the URL.
  // When the user lands on /chat (no :ticketId), the ticket is local-only
  // until first send; then the URL syncs so refresh / deep-link / the
  // ApprovalToastBroker's "is the user looking at this ticket?" check
  // all become first-class.
  const [urlSynced, setUrlSynced] = useState<boolean>(Boolean(initialTicketId));
  const [events, setEvents] = useState<TicketEventDTO[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [closing, setClosing] = useState(false);
  const streamRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset the transcript whenever the ticket changes (New button).
  useEffect(() => {
    setEvents([]);
  }, [ticketId]);

  // Stick to bottom on new content.
  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [events]);

  // Stream the ticket transcript. Events arrive in seq order; dedupe by
  // event_id so a reconnect (which replays from seq 0) doesn't double up.
  useSSE<TicketEventDTO>(`/tickets/${ticketId}/events`, (ev) => {
    if (!ev || !ev.event_id) return;
    setEvents((prev) =>
      prev.some((e) => e.event_id === ev.event_id)
        ? prev
        : [...prev, ev].sort((a, b) => a.seq - b.seq),
    );
  });

  // AUD.5c: pending approvals for THIS ticket render inline at the end
  // of the transcript — Claude Code style. The global ApprovalToastBroker
  // (AUD.5d) reads the same /approvals data and suppresses its popup
  // when the user is here, so the user sees exactly one surface per
  // pending approval.
  const { data: allApprovals, refresh: refreshApprovals } = usePolling(
    api.listApprovals, 1500,
  );
  const inlineApprovals: PendingApproval[] = (allApprovals ?? [])
    .filter((a) => a.ticket_id === ticketId)
    .sort((a, b) => a.requested_at - b.requested_at);

  // In-flight: the human has spoken but the main agent hasn't replied yet.
  // Until the reply streams token-by-token, show a live "working…" affordance
  // so a turn (esp. a simple one with no dispatches) doesn't look frozen.
  const lastHumanSeq = Math.max(-1, ...events.filter((e) => e.kind === "human_message").map((e) => e.seq));
  const lastMainReplySeq = Math.max(-1, ...events.filter((e) => e.kind === "agent_message" && e.actor === "main").map((e) => e.seq));
  const awaitingReply = sending || lastHumanSeq > lastMainReplySeq;
  const workingLabel = describeWork(events[events.length - 1], inlineApprovals.length > 0);

  const submit = async (text: string): Promise<void> => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    try {
      await api.sendTicketMessage(ticketId, trimmed);
      setInput("");
      // First-send URL sync: lift the local-state ticketId into the URL
      // so refresh preserves the conversation, deep-linking works, and
      // the ApprovalToastBroker can read window.location to decide
      // whether to render an approval inline-in-chat vs as a toast.
      // ``replace`` keeps Back working naturally (it goes to wherever
      // the user came from, not to /chat-with-no-id).
      if (!urlSynced) {
        navigate(`/chat/${encodeURIComponent(ticketId)}`, { replace: true });
        setUrlSynced(true);
      }
    } catch (err) {
      // Surface a local error line; the real transcript is server-driven.
      setEvents((prev) => [...prev, {
        ticket_id: ticketId,
        actor: "main",
        kind: "agent_message",
        payload: { text: `send failed: ${(err as Error).message}`, status: "failed" },
        seq: Number.MAX_SAFE_INTEGER,
        event_id: `local-error-${Date.now()}`,
        ts: Date.now() / 1000,
      }]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const resetConversation = (): void => {
    setTicketId(newTicketId());
    setUrlSynced(false);
    setInput("");
    inputRef.current?.focus();
  };

  // Close (resolve) the ticket: the backend summarizes it into long-term
  // memory and discards its checkpoints, then we start a fresh ticket.
  const closeAndReset = async (): Promise<void> => {
    if (events.length === 0 || closing) return;
    setClosing(true);
    try {
      await api.closeTicket(ticketId);
    } catch { /* best-effort; still start fresh */ }
    finally {
      setClosing(false);
      resetConversation();
    }
  };

  const send = (e: React.FormEvent): void => {
    e.preventDefault();
    void submit(input);
  };

  const participants = new Set(events.map((e) => e.actor).filter((a) => a !== "human"));

  return (
    <section className="flex min-h-0 h-full bg-dark-primary">
      {/* CHAT.1: sessions rail on the left (ChatGPT/Claude-style).
          Click to switch tickets, "+ New" creates a fresh local id. */}
      <SessionsRail currentTicketId={ticketId} onNew={resetConversation} />
      <div className="flex flex-col min-h-0 flex-1 bg-dark-primary">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border-subtle flex items-center justify-between">
        <div className="flex items-baseline gap-3">
          <Sparkles size={16} className="text-accent-green self-center" strokeWidth={2.25} />
          <h1 className="font-display text-base font-semibold text-text-primary">
            Conversation
          </h1>
          <span className="text-[11px] font-mono text-text-muted">
            group chat · main agent coordinates specialists
            {participants.size > 0 && (
              <> · {participants.size} agent(s) active</>
            )}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void closeAndReset()}
            disabled={events.length === 0 || closing}
            className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-blue border border-border-subtle hover:border-accent-blue/40 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            title="Resolve this ticket — summarize it to memory and start fresh"
          >
            <CheckCircle2 size={18} strokeWidth={2.5} className="text-accent-blue" />
            {closing ? "Closing…" : "Resolve"}
          </button>
          <button
            onClick={resetConversation}
            disabled={events.length === 0}
            className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-green border border-border-subtle hover:border-accent-green/40 rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            title="Start a new ticket (fresh group chat)"
          >
            <Plus size={18} strokeWidth={2.5} className="text-accent-green" />
            New
          </button>
        </div>
      </div>

      {/* Transcript */}
      <div
        ref={streamRef}
        id="chat-stream"
        className="flex-1 overflow-auto px-6 py-8 space-y-4"
      >
        {/* Keep EmptyChat mounted until the first transcript event arrives.
            Previously this also hid on ``sending``, so the first send would
            swap the big welcome for a tiny "opening ticket…" line until the
            SSE delivered the human-message echo — a visible blink. The
            disabled submit button + cleared input already convey "sent". */}
        {events.length === 0 && <EmptyChat onPick={submit} />}
        {events.map((ev) => <EventView key={ev.event_id} event={ev} />)}
        {inlineApprovals.map((a) => (
          <InlineApprovalCard key={a.approval_id} approval={a} onResolved={refreshApprovals} />
        ))}
        {awaitingReply && inlineApprovals.length === 0 && <ThinkingBubble label={workingLabel} />}
      </div>

      {/* Composer */}
      <form
        id="task-form"
        onSubmit={send}
        className="border-t border-border-subtle px-6 py-4"
      >
        <div className="max-w-4xl mx-auto flex gap-3">
          <input
            ref={inputRef}
            id="task-input"
            autoComplete="off"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Describe a task — the main agent will pull in the right specialists"
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
          ↵ submits · destructive tools surface an inline approval card
        </div>
      </form>
      </div>
    </section>
  );
}

function EmptyChat({ onPick }: { onPick: (text: string) => void }): JSX.Element {
  const examples = [
    "list pods in default namespace",
    "check disk space on every cluster node and flag anything over 80%",
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
          Describe an operation in plain English. The main agent coordinates
          the right specialists, who collaborate in this thread and route
          destructive actions through the approval queue.
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

// Derive a human "what's happening now" label from the latest event, shown
// in the thinking bubble while a turn is in flight. Until the reply streams,
// this is the user's progress signal.
function describeWork(last: TicketEventDTO | undefined, hasPendingApproval: boolean): string {
  if (hasPendingApproval) return "Waiting for your approval…";
  if (!last) return "Main is thinking…";
  const p = (last.payload ?? {}) as Record<string, unknown>;
  switch (last.kind) {
    case "dispatch": return `Dispatching to ${String(p.to ?? "a specialist")}…`;
    case "tool_call": return `${last.actor} · ${String(p.tool ?? "running a tool")}…`;
    case "agent_result": return "Main is synthesizing…";
    case "agent_message": return last.actor === "main" ? "Main is thinking…" : "Main is synthesizing…";
    default: return "Main is thinking…";
  }
}

// Animated "working…" bubble — same lane as a main-agent message so it reads
// as the reply forming. Removed the instant the agent_message arrives.
function ThinkingBubble({ label }: { label: string }): JSX.Element {
  return (
    <div className="flex gap-3" data-testid="thinking">
      <div className="shrink-0 w-8 h-8 rounded-full bg-accent-green/10 border border-accent-green/30 flex items-center justify-center">
        <Sparkles size={15} className="text-accent-green" strokeWidth={2.25} />
      </div>
      <div className="flex items-center gap-2 text-[13px] text-text-secondary pt-1">
        <span className="inline-flex gap-1" aria-hidden>
          {[0, 150, 300].map((d) => (
            <span
              key={d}
              className="w-1.5 h-1.5 rounded-full bg-accent-green/70 animate-bounce"
              style={{ animationDelay: `${d}ms` }}
            />
          ))}
        </span>
        <span className="font-mono">{label}</span>
      </div>
    </div>
  );
}

// Dispatch each transcript event to the right presentation.
function EventView({ event }: { event: TicketEventDTO }): JSX.Element | null {
  const p = (event.payload ?? {}) as Record<string, unknown>;
  switch (event.kind) {
    case "human_message":
      return <HumanBubble text={payloadText(event)} />;
    case "agent_message":
      if (p.type === "question" || p.type === "answer") {
        return <InterAgentLine event={event} />;
      }
      return <AgentBubble actor={event.actor} text={payloadText(event)} status={p.status as string | undefined} />;
    case "agent_result":
      return (
        <AgentBubble
          actor={event.actor}
          text={payloadText(event)}
          status={p.status as string | undefined}
          artifacts={p.artifacts as Record<string, unknown> | undefined}
        />
      );
    case "dispatch":
      return <DispatchChip to={String(p.to ?? "?")} subtask={payloadText(event)} />;
    case "tool_call":
      return <ToolCallLine event={event} />;
    case "approval_request":
      // AUD.5c: pending approvals render as an inline ApprovalCard at
      // the END of the transcript via ChatPage's polling, not as a
      // transcript event. This case fires only if the runtime ever
      // publishes approval_request to the bus (unused today). Show a
      // muted notice so the timeline still shows when it was requested.
      return <NoticeLine text={`${event.actor} requested approval for ${String(p.tool ?? "a tool")}`} />;
    case "approval_decision":
      return <NoticeLine text={`approval ${p.approved ? "granted" : "denied"} for ${event.actor}`} />;
    default:
      return null;
  }
}

function HumanBubble({ text }: { text: string }): JSX.Element {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] flex items-start gap-2 flex-row-reverse">
        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-dark-control border border-border-subtle flex items-center justify-center">
          <User size={14} strokeWidth={2.25} className="text-text-secondary" />
        </div>
        <div className="bg-accent-blue/[0.08] border border-accent-blue/20 rounded-md rounded-tr-sm px-4 py-2.5 text-sm text-text-primary whitespace-pre-wrap break-words">
          {text}
        </div>
      </div>
    </div>
  );
}

function AgentBubble({ actor, text, status, artifacts }: { actor: string; text: string; status?: string; artifacts?: Record<string, unknown> }): JSX.Element {
  const accent = actorAccent(actor);
  const failed = status === "failed" || status === "rejected";
  return (
    <div className="flex">
      <div className="max-w-[80%] flex items-start gap-2">
        <div className={clsx("flex-shrink-0 w-7 h-7 rounded-full border flex items-center justify-center", accent.ring)}>
          <Bot size={14} strokeWidth={2.25} className={accent.text} />
        </div>
        <div className="flex-1">
          <div className={clsx("text-[10px] font-mono uppercase tracking-[1.5px] mb-1 pl-1", accent.text)}>
            {accent.label}
          </div>
          <div
            className={clsx(
              "rounded-md rounded-tl-sm px-4 py-3",
              failed ? "bg-accent-red/10 border border-accent-red/30" : "bg-dark-panel border border-border-subtle",
            )}
          >
            {text ? <CollapsibleProse text={text} /> : <span className="text-text-muted italic text-sm">(no content)</span>}
            {artifacts && Object.keys(artifacts).length > 0 && <ArtifactsDetails artifacts={artifacts} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function ArtifactsDetails({ artifacts }: { artifacts: Record<string, unknown> }): JSX.Element {
  return (
    <details className="mt-2 border-t border-border-subtle/60 pt-2">
      <summary className="text-[11px] font-mono text-text-muted cursor-pointer hover:text-text-secondary">
        data
      </summary>
      <pre className="mt-2 text-[11px] font-mono text-text-secondary bg-dark-primary border border-border-subtle rounded p-2 overflow-auto max-h-80">
        {JSON.stringify(artifacts, null, 2)}
      </pre>
    </details>
  );
}

function DispatchChip({ to, subtask }: { to: string; subtask: string }): JSX.Element {
  const accent = actorAccent(to);
  return (
    <div className="flex justify-center">
      <div className="flex items-center gap-2 text-[11px] font-mono text-text-muted border border-border-subtle rounded-full px-3 py-1">
        <span className="text-accent-green">main</span>
        <ArrowRight size={11} />
        <span className={accent.text}>{to}</span>
        <span className="text-text-secondary truncate max-w-[28rem]">{subtask}</span>
      </div>
    </div>
  );
}

function InterAgentLine({ event }: { event: TicketEventDTO }): JSX.Element {
  const p = (event.payload ?? {}) as Record<string, unknown>;
  const isQuestion = p.type === "question";
  const other = String(p.to ?? "?");
  const accent = actorAccent(event.actor);
  return (
    <div className="flex justify-center">
      <div className="flex items-center gap-2 text-[11px] font-mono bg-dark-secondary/30 border border-border-subtle/60 rounded px-3 py-1 max-w-[80%]">
        <span className={accent.text}>{event.actor}</span>
        <span className="text-text-muted">{isQuestion ? "asks" : "→"}</span>
        <span className={actorAccent(other).text}>{other}</span>
        <span className="text-text-secondary truncate">{payloadText(event)}</span>
      </div>
    </div>
  );
}

function ToolCallLine({ event }: { event: TicketEventDTO }): JSX.Element {
  const p = (event.payload ?? {}) as Record<string, unknown>;
  const accent = actorAccent(event.actor);
  const approved = p.approved;
  return (
    <details className="flex">
      <summary className="flex items-center gap-2 text-[11px] font-mono text-text-muted cursor-pointer hover:text-text-secondary list-none pl-9">
        <Wrench size={11} className={accent.text} />
        <span className={accent.text}>{event.actor}</span>
        <span className="text-text-primary">{String(p.tool ?? "tool")}</span>
        {approved === true && <span className="text-accent-green">approved</span>}
        {approved === false && <span className="text-accent-red">rejected</span>}
      </summary>
      <pre className="mt-1 ml-9 text-[11px] font-mono text-text-secondary bg-dark-primary border border-border-subtle rounded p-2 overflow-auto max-h-60">
        {JSON.stringify({ args: p.args, result: p.result }, null, 2)}
      </pre>
    </details>
  );
}

function NoticeLine({ text }: { text: string }): JSX.Element {
  return (
    <div className="flex justify-center">
      <div className="flex items-center gap-2 text-[11px] font-mono text-accent-yellow bg-accent-yellow/5 border border-accent-yellow/20 rounded px-3 py-1">
        <AlertCircle size={11} />
        {text}
      </div>
    </div>
  );
}


/**
 * AUD.5c — inline approval card rendered in the chat transcript for
 * any pending /approvals whose ticket_id matches the current chat
 * ticket. Claude-Code-style: full rationale + args + diff visible by
 * default, three actions (Approve / Decline / Details toggle for the
 * raw args dict). One-click approve/decline with a stock reason; users
 * who want to write a longer reason can use the Approval queue in
 * /auditing.
 */
export function InlineApprovalCard({
  approval,
  onResolved,
}: {
  approval: PendingApproval;
  onResolved: () => void;
}): JSX.Element {
  const [showArgs, setShowArgs] = useState(false);
  const [busy, setBusy] = useState<"approve" | "decline" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const resolve = async (approved: boolean): Promise<void> => {
    setBusy(approved ? "approve" : "decline");
    setErr(null);
    try {
      await api.resolveApproval(approval.approval_id, {
        approved,
        reason: approved ? "approved inline in chat" : "declined inline in chat",
      });
      onResolved();
    } catch (e) {
      setErr((e as Error).message);
      setBusy(null);
    }
  };

  return (
    <div className="flex">
      <div className="max-w-[80%] flex items-start gap-2">
        <div className="flex-shrink-0 w-7 h-7 rounded-full border border-accent-yellow/40 bg-accent-yellow/10 flex items-center justify-center">
          <ShieldAlert size={14} className="text-accent-yellow" strokeWidth={2.25} />
        </div>
        <div className="flex-1">
          <div className="text-[10px] font-mono uppercase tracking-[1.5px] mb-1 pl-1 text-accent-yellow">
            Approval needed · {approval.agent} → {approval.tool}
          </div>
          <div
            data-testid={`inline-approval-${approval.approval_id}`}
            className="rounded-md rounded-tl-sm bg-accent-yellow/[0.06] border border-accent-yellow/30 px-4 py-3 space-y-2"
          >
            <p className="text-sm text-text-primary leading-snug">{approval.rationale}</p>
            {approval.diff && (
              <pre className="font-mono text-[11px] text-text-secondary bg-dark-primary/60 border border-border-subtle rounded p-2 overflow-auto max-h-60">
                {approval.diff}
              </pre>
            )}
            <button
              type="button"
              onClick={() => setShowArgs((v) => !v)}
              className="flex items-center gap-1 text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted hover:text-text-secondary"
            >
              <ChevronDown size={14} strokeWidth={2.25} className={clsx("transition-transform", showArgs && "rotate-180")} />
              {showArgs ? "Hide" : "Show"} raw args
            </button>
            {showArgs && (
              <pre className="font-mono text-[11px] text-text-secondary bg-dark-primary/60 border border-border-subtle rounded p-2 overflow-auto max-h-40">
                {JSON.stringify(approval.args, null, 2)}
              </pre>
            )}
            {err && (
              <div className="flex items-center gap-1 text-[11px] text-accent-red">
                <AlertCircle size={11} />
                {err}
              </div>
            )}
            <div className="flex gap-2 pt-1">
              <button
                onClick={() => void resolve(true)}
                disabled={busy !== null}
                className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <CheckCircle2 size={14} strokeWidth={2.5} />
                {busy === "approve" ? "Approving…" : "Approve"}
              </button>
              <button
                onClick={() => void resolve(false)}
                disabled={busy !== null}
                className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-red border border-accent-red/40 hover:bg-accent-red/10 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <X size={14} strokeWidth={2.5} />
                {busy === "decline" ? "Declining…" : "Decline"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Renders message text with a "show more / show less" toggle when it's
 * long, so a single huge dump doesn't blow up the chat box.
 */
const COLLAPSE_AT_CHARS = 600;
export function CollapsibleProse({ text }: { text: string }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const tooLong = text.length > COLLAPSE_AT_CHARS;
  const visible = expanded || !tooLong ? text : text.slice(0, COLLAPSE_AT_CHARS) + "…";

  return (
    <div className="space-y-2">
      <div className={clsx("prose max-w-none", !expanded && tooLong && "max-h-64 overflow-hidden")}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{visible}</ReactMarkdown>
      </div>
      {tooLong && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-green transition-colors"
        >
          {expanded ? "show less ↑" : `show more ↓ (${text.length - COLLAPSE_AT_CHARS} more chars)`}
        </button>
      )}
    </div>
  );
}
