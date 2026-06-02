import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { TerminalSquare, Plus, Trash2, ArrowRight, Circle } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import { NewTerminalSessionModal } from "../components/TerminalSessionsRail";
import type { TerminalSession } from "../types";

/**
 * TERM.3a — Terminal landing page.
 *
 * Lists this user's live sessions. "+ New session" opens a modal with a
 * host picker (from the inventory) + optional ssh_user override. Clicking
 * a session navigates to /terminal/{session_id} where TERM.3b will mount
 * xterm.js + the WebSocket client.
 *
 * Sessions are polled every 3s so an externally-killed session disappears
 * from the list without the user refreshing.
 */
export function TerminalPage(): JSX.Element {
  const navigate = useNavigate();
  const { data, error, refresh } = usePolling(api.listTerminalSessions, 3000);
  const sessions = data ?? [];
  const [modalOpen, setModalOpen] = useState(false);

  const onDelete = async (id: string): Promise<void> => {
    if (!window.confirm("Close this terminal session? Any unsaved work in the ssh shell will be lost.")) return;
    try {
      await api.removeTerminalSession(id);
      refresh();
    } catch (e) {
      window.alert(`Could not close: ${(e as Error).message}`);
    }
  };

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 py-3 border-b border-border-subtle flex items-baseline gap-3">
        <TerminalSquare size={16} className="text-accent-green self-center" strokeWidth={2.25} />
        <h1 className="font-display text-base font-semibold text-text-primary">Terminal</h1>
        <span className="text-[11px] font-mono text-text-muted">
          interactive ssh sessions hosted on the dashboard pod
        </span>
        <div className="flex-1" />
        <button
          onClick={() => setModalOpen(true)}
          data-testid="terminal-new-session"
          className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors"
        >
          <Plus size={16} strokeWidth={2.5} />
          New session
        </button>
      </div>

      <div className="flex-1 overflow-auto px-6 py-6 max-w-5xl mx-auto w-full space-y-2">
        {error && (
          <div className="text-sm text-accent-red font-mono bg-accent-red/10 border border-accent-red/40 rounded px-3 py-2">
            {error.message}
          </div>
        )}

        {sessions.length === 0 && !error && (
          <div className="text-center text-text-secondary py-16">
            <TerminalSquare size={28} className="mx-auto mb-3 text-text-muted" strokeWidth={2.25} />
            <p className="text-sm">No live sessions. Click "+ New session" to open one.</p>
          </div>
        )}

        {sessions.map((s) => (
          <SessionRow key={s.session_id} session={s}
                      onOpen={() => navigate(`/terminal/${encodeURIComponent(s.session_id)}`)}
                      onDelete={() => void onDelete(s.session_id)} />
        ))}
      </div>

      <NewTerminalSessionModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreated={(s) => {
          setModalOpen(false);
          refresh();
          navigate(`/terminal/${encodeURIComponent(s.session_id)}`);
        }}
      />
    </section>
  );
}


function SessionRow({
  session, onOpen, onDelete,
}: {
  session: TerminalSession;
  onOpen: () => void;
  onDelete: () => void;
}): JSX.Element {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onOpen(); }}
      data-testid={`terminal-row-${session.session_id}`}
      className={clsx(
        "bg-dark-panel border rounded-md px-4 py-3 flex items-center gap-3 transition-colors cursor-pointer",
        session.alive
          ? "border-border-subtle hover:border-accent-green/40"
          : "border-accent-red/40 opacity-60",
      )}
    >
      <Circle
        size={10}
        className={clsx(
          session.alive ? "text-accent-green" : "text-accent-red",
          session.attached && "animate-pulse",
        )}
        fill="currentColor"
        strokeWidth={0}
      />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="font-mono text-sm font-semibold text-text-primary">{session.host_alias}</span>
          <span className="font-mono text-[11px] text-text-secondary">
            {session.kind ? "(local CLI)" : `${session.ssh_user}@${session.address}`}
          </span>
          {session.kind && (
            <span className="font-mono text-[10px] uppercase tracking-[1.5px] text-accent-green">
              {session.kind}
            </span>
          )}
          {session.attached && (
            <span className="font-mono text-[10px] uppercase tracking-[1.5px] text-accent-green">
              attached
            </span>
          )}
          {!session.alive && (
            <span className="font-mono text-[10px] uppercase tracking-[1.5px] text-accent-red">
              dead
            </span>
          )}
        </div>
        <div className="font-mono text-[10px] text-text-muted mt-1">
          id <code>{session.session_id.slice(0, 12)}</code>
          {" · "}
          opened {new Date(session.created_at * 1000).toLocaleString()}
        </div>
      </div>
      <ArrowRight size={16} className="text-text-muted shrink-0" strokeWidth={2.25} />
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="text-text-muted hover:text-accent-red p-1"
        aria-label="Close session"
      >
        <Trash2 size={16} strokeWidth={2.25} />
      </button>
    </div>
  );
}


export { TerminalSessionPage } from "./TerminalSessionPage";
