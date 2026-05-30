import { useNavigate } from "react-router-dom";
import { MessageSquare, Plus } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import type { TicketSummary } from "../types";

/**
 * CHAT.1 — left rail listing past group-chat sessions, rendered inside
 * ChatPage (ChatGPT / Claude-style). Selecting a session navigates the
 * chat to /chat/{ticket_id}. "+ New" calls back to ChatPage's
 * resetConversation (which generates a fresh ticket id and clears
 * urlSynced so the first-send navigate fires on the next message).
 *
 * Polls every 5s so a session that just got its first message shows
 * up in the list without a manual refresh.
 */
export function SessionsRail({
  currentTicketId,
  onNew,
}: {
  currentTicketId: string;
  onNew: () => void;
}): JSX.Element {
  const navigate = useNavigate();
  const { data } = usePolling(api.listTickets, 5000);
  const tickets: TicketSummary[] = data ?? [];

  return (
    <aside
      data-testid="sessions-rail"
      className="w-[280px] flex-shrink-0 bg-dark-secondary border-r border-border-subtle flex flex-col min-h-0"
    >
      <div className="px-3 py-3 border-b border-border-subtle">
        <button
          onClick={onNew}
          data-testid="sessions-rail-new"
          className="w-full flex items-center justify-center gap-2 px-3 py-2 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors"
        >
          <Plus size={16} strokeWidth={2.5} />
          New conversation
        </button>
      </div>
      <div className="flex-1 overflow-auto p-2 space-y-1">
        {tickets.length === 0 && (
          <div className="text-[11px] font-mono text-text-muted italic px-2 py-4 text-center">
            no past sessions yet
          </div>
        )}
        {tickets.map((t) => {
          const active = t.ticket_id === currentTicketId;
          return (
            <button
              key={t.ticket_id}
              onClick={() => navigate(`/chat/${encodeURIComponent(t.ticket_id)}`)}
              data-testid={`sessions-rail-row-${t.ticket_id}`}
              className={clsx(
                "w-full text-left rounded-md px-2.5 py-2 transition-colors border",
                active
                  ? "bg-accent-green/[0.07] border-accent-green/40"
                  : "border-transparent hover:bg-dark-tertiary/40 hover:border-border-subtle",
              )}
            >
              <div className="text-[12px] text-text-primary leading-snug line-clamp-2">
                {t.first_message || (
                  <span className="italic text-text-muted">(no message)</span>
                )}
              </div>
              <div className="text-[10px] font-mono text-text-muted mt-1 flex items-center gap-1.5 flex-wrap">
                <MessageSquare size={10} className="flex-shrink-0" />
                <span>{t.event_count}</span>
                <span>·</span>
                <span>{relativeTime(t.last_ts)}</span>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}


/** Compact human-readable "5m ago" / "2h ago" / "Mar 4" formatting.
 *  Exported so the unit test can pin the boundaries. */
export function relativeTime(unixSeconds: number, now = Date.now() / 1000): string {
  const diffSec = Math.max(0, now - unixSeconds);
  if (diffSec < 60) return "now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`;
  return new Date(unixSeconds * 1000).toLocaleDateString();
}
