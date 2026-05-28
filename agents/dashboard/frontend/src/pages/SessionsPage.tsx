import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { History, MessageSquare, ArrowRight } from "lucide-react";
import { api } from "../api";
import type { TicketSummary } from "../types";

/**
 * Sessions — a list of past group-chat tickets. Each row opens that
 * ticket back up in the Chat view (which replays its transcript from
 * the SSE stream).
 */
export function SessionsPage(): JSX.Element {
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    api.listTickets()
      .then((t) => { if (!cancelled) setTickets(t); })
      .catch((e) => { if (!cancelled) setError((e as Error).message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 py-3 border-b border-border-subtle flex items-center gap-3 bg-dark-secondary/40">
        <History size={16} className="text-accent-green" strokeWidth={2.25} />
        <h1 className="font-display text-base font-semibold text-text-primary">Sessions</h1>
        <span className="text-[11px] font-mono text-text-muted">
          past tickets · click to reopen
          {tickets.length > 0 && <> · {tickets.length}</>}
        </span>
      </div>

      <div className="flex-1 overflow-auto px-6 py-6 space-y-2 max-w-4xl mx-auto w-full">
        {loading && <div className="text-sm text-text-muted font-mono">loading sessions…</div>}
        {error && <div className="text-sm text-accent-red font-mono">failed to load: {error}</div>}
        {!loading && !error && tickets.length === 0 && (
          <div className="text-center text-text-secondary py-16">
            <MessageSquare size={28} className="mx-auto mb-3 text-text-muted" />
            <p className="text-sm">No sessions yet. Start a conversation in the Chat tab.</p>
          </div>
        )}
        {tickets.map((t) => (
          <button
            key={t.ticket_id}
            onClick={() => navigate(`/chat/${encodeURIComponent(t.ticket_id)}`)}
            className="w-full text-left bg-dark-panel border border-border-subtle hover:border-accent-green/40 rounded-md px-4 py-3 transition-colors group flex items-center gap-3"
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm text-text-primary truncate">
                {t.first_message || <span className="italic text-text-muted">(no message)</span>}
              </div>
              <div className="text-[11px] font-mono text-text-muted mt-1 flex items-center gap-2 flex-wrap">
                <code>{t.ticket_id.slice(0, 12)}</code>
                <span>·</span>
                <span>{t.event_count} event{t.event_count === 1 ? "" : "s"}</span>
                <span>·</span>
                <span>last: {t.last_actor}</span>
                <span>·</span>
                <span>{new Date(t.last_ts * 1000).toLocaleString()}</span>
              </div>
            </div>
            <ArrowRight size={16} className="text-text-muted group-hover:text-accent-green flex-shrink-0" />
          </button>
        ))}
      </div>
    </section>
  );
}
