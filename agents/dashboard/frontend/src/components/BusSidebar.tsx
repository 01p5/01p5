import { useRef, useState } from "react";
import { Activity } from "lucide-react";
import { useSSE } from "../hooks/useSSE";
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

const MAX_EVENTS = 200;

export function BusSidebar(): JSX.Element {
  const [events, setEvents] = useState<BusEvent[]>([]);
  const initialReplayDone = useRef(false);

  useSSE<BusEvent>("/events", (ev) => {
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
        <Activity size={14} className="text-accent-blue" />
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
          <div
            key={ev.msg_id}
            className="event font-mono text-[11px] leading-snug px-2 py-1 hover:bg-dark-tertiary/40 rounded border-b border-border-subtle/40"
          >
            <span className="text-text-muted">
              {new Date(ev.timestamp * 1000).toLocaleTimeString()}
            </span>{" "}
            <span className={clsx("kind", KIND_TONE[ev.kind])}>{ev.kind}</span>{" "}
            <span className="text-text-secondary">{ev.sender}</span>
            <span className="text-text-muted"> → </span>
            <span className="text-text-secondary">{ev.recipient}</span>
            <div className="text-text-muted truncate" title={formatPayload(ev.payload)}>
              {formatPayload(ev.payload)}
            </div>
          </div>
        ))}
      </div>
    </aside>
  );
}
