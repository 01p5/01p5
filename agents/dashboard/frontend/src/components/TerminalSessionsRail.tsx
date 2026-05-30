import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Circle, TerminalSquare } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import { Modal } from "./Modal";
import type { InventoryHost, TerminalSession } from "../types";


/**
 * TERM.8 — left rail listing live terminal sessions, rendered inside
 * the per-session view (`/terminal/{id}`). Same pattern as ChatPage's
 * SessionsRail: lets the user switch terminals without bouncing back
 * to the `/terminal` landing page.
 *
 * Polls every 3s so a session that gets closed elsewhere disappears
 * from the rail without a refresh.
 *
 * "+ New" opens the NewTerminalSessionModal (extracted from
 * TerminalPage so both surfaces share it).
 */
export function TerminalSessionsRail({
  currentSessionId,
}: {
  currentSessionId: string;
}): JSX.Element {
  const navigate = useNavigate();
  const { data, refresh } = usePolling(api.listTerminalSessions, 3000);
  const sessions = data ?? [];
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <aside
      data-testid="terminal-sessions-rail"
      className="w-[260px] flex-shrink-0 bg-dark-secondary border-r border-border-subtle flex flex-col min-h-0"
    >
      <div className="px-3 py-3 border-b border-border-subtle">
        <button
          onClick={() => setModalOpen(true)}
          data-testid="terminal-rail-new"
          className="w-full flex items-center justify-center gap-2 px-3 py-2 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors"
        >
          <Plus size={16} strokeWidth={2.5} />
          New session
        </button>
      </div>
      <div className="flex-1 overflow-auto p-2 space-y-1">
        {sessions.length === 0 && (
          <div className="text-[11px] font-mono text-text-muted italic text-center py-6">
            no live sessions
          </div>
        )}
        {sessions.map((s) => {
          const active = s.session_id === currentSessionId;
          return (
            <button
              key={s.session_id}
              onClick={() => navigate(`/terminal/${encodeURIComponent(s.session_id)}`)}
              data-testid={`terminal-rail-row-${s.session_id}`}
              className={clsx(
                "w-full text-left rounded-md px-2.5 py-2 transition-colors border flex items-center gap-2 min-w-0",
                active
                  ? "bg-accent-green/[0.07] border-accent-green/40"
                  : "border-transparent hover:bg-dark-tertiary/40 hover:border-border-subtle",
              )}
            >
              <Circle
                size={8}
                fill="currentColor"
                strokeWidth={0}
                className={clsx(
                  "shrink-0",
                  s.alive ? "text-accent-green" : "text-accent-red",
                  s.attached && "animate-pulse",
                )}
              />
              <div className="flex-1 min-w-0">
                <div className="font-mono text-[12px] text-text-primary truncate">{s.host_alias}</div>
                <div className="font-mono text-[10px] text-text-muted truncate">
                  {s.ssh_user}@{s.address}
                </div>
              </div>
            </button>
          );
        })}
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
    </aside>
  );
}


/**
 * NewTerminalSessionModal — extracted from TerminalPage in TERM.8 so
 * both the landing page (`/terminal`) and the per-session rail
 * (`/terminal/{id}`) reuse the same host-picker + ssh_user-override
 * form. Exported so TerminalPage can import it; both call sites
 * navigate to the new session id on create.
 */
export function NewTerminalSessionModal({
  open, onClose, onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (s: TerminalSession) => void;
}): JSX.Element {
  const [hosts, setHosts] = useState<InventoryHost[]>([]);
  const [selectedHost, setSelectedHost] = useState<string>("");
  const [sshUserOverride, setSshUserOverride] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    setBusy(false);
    api.listHosts()
      .then((h) => {
        setHosts(h);
        if (h.length > 0 && !selectedHost) setSelectedHost(h[0].name);
      })
      .catch((e) => setErr((e as Error).message));
  }, [open, selectedHost]);

  const onCreate = async (): Promise<void> => {
    if (!selectedHost) { setErr("pick a host"); return; }
    setBusy(true);
    setErr(null);
    try {
      const s = await api.createTerminalSession({
        host_alias: selectedHost,
        ssh_user: sshUserOverride.trim() || undefined,
      });
      onCreated(s);
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  const hostObj = hosts.find((h) => h.name === selectedHost);
  const inputCls = "w-full bg-dark-primary border border-border-subtle rounded px-2.5 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-green/60";

  return (
    <Modal open={open} onClose={onClose} title="Open new terminal session">
      <div className="space-y-3">
        <Field label="Host (from inventory)" hint="open /hosts to add more">
          {hosts.length === 0 ? (
            <div className="text-[11px] font-mono text-text-muted italic px-2 py-3 border border-dashed border-border-subtle rounded">
              No hosts in the inventory yet — add one in the Hosts tab first.
            </div>
          ) : (
            <select
              value={selectedHost}
              onChange={(e) => setSelectedHost(e.target.value)}
              data-testid="terminal-host-select"
              className={inputCls}
            >
              {hosts.map((h) => (
                <option key={h.id} value={h.name}>
                  {h.name} — {h.ssh_user}@{h.address}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="SSH user override"
               hint={hostObj
                 ? `leave blank → use ${hostObj.ssh_user} from the host`
                 : "leave blank for the host's default"}>
          <input
            value={sshUserOverride}
            onChange={(e) => setSshUserOverride(e.target.value)}
            placeholder={hostObj?.ssh_user ?? "ubuntu"}
            data-testid="terminal-ssh-user-override"
            className={inputCls}
          />
        </Field>
        {err && <div className="text-sm text-accent-red font-mono">{err}</div>}
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose}
                  className="px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-text-primary">
            Cancel
          </button>
          <button
            onClick={() => void onCreate()}
            disabled={busy || hosts.length === 0}
            data-testid="terminal-create-confirm"
            className="px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded disabled:opacity-40"
          >
            {busy ? "Opening…" : "Open"}
          </button>
        </div>
      </div>
    </Modal>
  );
}


function Field({ label, hint, children }: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="space-y-1">
      <label className="block text-[10px] font-mono uppercase tracking-[1.5px] text-text-secondary">
        {label}
        {hint && <span className="ml-2 normal-case tracking-normal text-text-muted">— {hint}</span>}
      </label>
      {children}
    </div>
  );
}


/** Re-export so existing tests / callers can find it by the type;
 *  the icon is purely decorative inside the rail header. */
export { TerminalSquare };
