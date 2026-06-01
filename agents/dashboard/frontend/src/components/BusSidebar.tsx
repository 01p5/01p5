import { useRef, useState } from "react";
import { Activity } from "lucide-react";
import { useSSE } from "../hooks/useSSE";
import { Modal } from "./Modal";
import type { BusEvent } from "../types";
import clsx from "clsx";

const KIND_TONE: Record<BusEvent["kind"], string> = {
  task: "text-accent-blue",
  result: "text-accent-green",
  progress: "text-text-muted",
  log: "text-text-muted",
  approval_request: "text-accent-yellow",
  approval_decision: "text-accent-orange",
};

/** Tiny inline-payload formatter. Strings stay; objects get a short JSON tag. */
function formatPayload(p: unknown): string {
  if (p === null || p === undefined) return "";
  if (typeof p === "string") return p;
  try { return JSON.stringify(p); } catch { return String(p); }
}

/** Pretty multi-line payload for the detail modal. */
function prettyPayload(p: unknown): string {
  if (p === null || p === undefined) return "(empty)";
  if (typeof p === "string") return p;
  try { return JSON.stringify(p, null, 2); } catch { return String(p); }
}

const MAX_EVENTS = 200;

export function BusSidebar(): JSX.Element {
  const [events, setEvents] = useState<BusEvent[]>([]);
  const [selected, setSelected] = useState<BusEvent | null>(null);
  const initialReplayDone = useRef(false);

  useSSE<BusEvent>("/events", (ev) => {
    // Filter direct-tool traffic — every /tools/{agent}/{tool} POST from
    // the dashboard's UI (e.g. KubernetesPage auto-refresh) publishes a
    // human <-> agent bus pair that swamps the feed. Group-chat traffic
    // uses "*" recipients and never has "human" as actor.
    if (ev.sender === "human" || ev.recipient === "human") return;
    setEvents((prev) => {
      // Newest first, capped.
      const next = [ev, ...prev];
      if (next.length > MAX_EVENTS) next.length = MAX_EVENTS;
      return next;
    });
    initialReplayDone.current = true;
  });

  return (
    <aside className="bg-dark-secondary border-r border-border-subtle flex flex-col min-h-0">
      <header className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
        <Activity size={15} className="text-accent-blue" strokeWidth={2.25} />
        <h2 className="font-display text-[11px] font-semibold uppercase tracking-[1.5px] text-text-secondary">
          Live activity
        </h2>
        <span className="text-[10px] font-mono text-text-muted ml-auto">
          {events.length}
        </span>
      </header>
      <div id="events" className="flex-1 overflow-auto p-2 space-y-px">
        {events.length === 0 && (
          <div className="text-text-muted italic text-xs p-2">no events yet</div>
        )}
        {events.map((ev) => (
          <button
            key={ev.msg_id}
            type="button"
            onClick={() => setSelected(ev)}
            title="Click for full event details"
            className="event w-full text-left font-mono text-[11px] leading-snug px-2 py-1 hover:bg-dark-tertiary/40 hover:border-accent-blue/30 rounded border-b border-border-subtle/40 cursor-pointer transition-colors"
          >
            <span className="text-text-muted">
              {new Date(ev.timestamp * 1000).toLocaleTimeString()}
            </span>{" "}
            <span className={clsx("kind", KIND_TONE[ev.kind])}>{ev.kind}</span>{" "}
            <span className="text-text-secondary">{ev.sender}</span>
            <span className="text-text-muted"> → </span>
            <span className="text-text-secondary">{ev.recipient}</span>
            <div className="text-text-muted truncate">
              {formatPayload(ev.payload)}
            </div>
          </button>
        ))}
      </div>

      <EventDetailModal event={selected} onClose={() => setSelected(null)} />
    </aside>
  );
}


/** Detail modal for a single bus event — the full record the truncated
 *  row can't show: ids, the kind/sender/recipient routing, the exact
 *  timestamp, and the pretty-printed payload. */
function EventDetailModal({ event, onClose }: {
  event: BusEvent | null;
  onClose: () => void;
}): JSX.Element {
  return (
    <Modal open={event !== null} onClose={onClose} title="Event detail">
      {event && (
        <div className="space-y-4" data-testid="event-detail">
          <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-[12px]">
            <Field label="kind">
              <span className={clsx("font-mono", KIND_TONE[event.kind])}>{event.kind}</span>
            </Field>
            <Field label="from">{event.sender}</Field>
            <Field label="to">{event.recipient}</Field>
            <Field label="time">{new Date(event.timestamp * 1000).toLocaleString()}</Field>
            <Field label="task_id"><code className="break-all">{event.task_id}</code></Field>
            <Field label="msg_id"><code className="break-all">{event.msg_id}</code></Field>
            {event.causation_id && (
              <Field label="caused by"><code className="break-all">{event.causation_id}</code></Field>
            )}
          </dl>
          <div>
            <div className="text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
              payload
            </div>
            <pre className="font-mono text-[11px] leading-snug text-text-primary bg-dark-primary border border-border-subtle rounded p-3 overflow-auto max-h-[48vh] whitespace-pre-wrap break-words">
              {prettyPayload(event.payload)}
            </pre>
          </div>
        </div>
      )}
    </Modal>
  );
}


function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <>
      <dt className="font-mono text-[10px] uppercase tracking-[1px] text-text-muted pt-0.5">{label}</dt>
      <dd className="text-text-primary font-mono break-words">{children}</dd>
    </>
  );
}
