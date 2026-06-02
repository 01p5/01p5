import { useCallback, useState } from "react";
import { ShieldCheck, RefreshCw, Pencil, Check, X, AlertCircle } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import { formatUsd } from "../components/CostChip";
import type { AdminUserAccounting } from "../types";


/**
 * ADM.4 — super-admin accounting dashboard.
 *
 * Visible only to super-admins (the nav tab is gated on /me.is_admin,
 * and every endpoint here 403s a non-admin regardless). Three things:
 *   - per-user cost + token + task rollup
 *   - today's spend vs each user's configured daily cap (editable)
 *   - a cross-user recent-activity feed
 *
 * Read endpoints poll; the limit editor PUTs then refreshes.
 */
export function AdminPage(): JSX.Element {
  const { data: accounting, error: acctErr, refresh } = usePolling(api.adminAccounting, 5000);
  const { data: activity } = usePolling(api.adminActivity, 5000);

  if (acctErr) {
    return (
      <section className="flex flex-col min-h-0 h-full bg-dark-primary items-center justify-center">
        <div data-testid="admin-error" className="max-w-md bg-accent-red/[0.06] border border-accent-red/40 rounded-md p-5 flex items-start gap-2">
          <AlertCircle size={18} className="text-accent-red" strokeWidth={2.25} />
          <div>
            <div className="font-semibold text-text-primary">Admin data unavailable</div>
            <code className="text-[11px] text-text-secondary break-all">{acctErr.message}</code>
          </div>
        </div>
      </section>
    );
  }

  const users = accounting?.users ?? [];

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 pt-6 pb-3 max-w-6xl mx-auto w-full flex items-baseline gap-3">
        <ShieldCheck size={16} className="text-accent-purple self-center" strokeWidth={2.25} />
        <h1 className="font-display text-xl font-semibold text-text-primary">Admin · Accounting</h1>
        <span className="text-[11px] font-mono text-text-muted">
          per-user cost · daily limits · activity — super-admin only
        </span>
        <button
          onClick={() => void refresh()}
          aria-label="Refresh"
          className="ml-auto p-1 text-text-muted hover:text-text-primary transition-colors"
        >
          <RefreshCw size={14} strokeWidth={2.25} />
        </button>
      </div>

      <div className="flex-1 overflow-auto px-6 py-5 space-y-6 max-w-6xl w-full mx-auto">
        {accounting?.default_daily_limit_usd != null && (
          <div data-testid="admin-default-limit" className="text-[11px] font-mono text-text-muted">
            default daily limit: <span className="text-text-secondary">{formatUsd(accounting.default_daily_limit_usd)}</span> / user
            <span className="ml-1">— applies unless an explicit override is set below</span>
          </div>
        )}
        <UsersTable users={users} onChanged={refresh} />
        <ActivityFeed items={activity?.activity ?? []} />
      </div>
    </section>
  );
}


function UsersTable({ users, onChanged }: { users: AdminUserAccounting[]; onChanged: () => void }): JSX.Element {
  return (
    <div>
      <h2 className="font-display text-[11px] font-semibold uppercase tracking-[1.5px] text-text-secondary mb-2">
        Users ({users.length})
      </h2>
      <div className="border border-border-subtle rounded-md overflow-hidden">
        <table className="w-full text-[12px]" data-testid="admin-users-table">
          <thead>
            <tr className="bg-dark-secondary/60 text-text-muted font-mono text-[10px] uppercase tracking-[1px]">
              <th className="text-left px-3 py-2">User</th>
              <th className="text-right px-3 py-2">Last login</th>
              <th className="text-right px-3 py-2">Tasks</th>
              <th className="text-right px-3 py-2" title="Daily usage — resets at 00:00 UTC">Daily usage</th>
              <th className="text-right px-3 py-2" title="Daily limit — daily usage must stay under this">Daily limit</th>
              <th className="text-right px-3 py-2" title="Total spend over all time">Total usage</th>
              <th className="text-right px-3 py-2">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {users.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-text-muted italic">
                no users yet — they appear here on first login
              </td></tr>
            )}
            {users.map((u) => (
              <UserRow key={u.email} user={u} onChanged={onChanged} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


function UserRow({ user, onChanged }: { user: AdminUserAccounting; onChanged: () => void }): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<string>(
    user.daily_limit_usd != null ? String(user.daily_limit_usd) : "",
  );
  const [saving, setSaving] = useState(false);

  const overLimit = user.effective_limit_usd != null && user.spent_today_usd >= user.effective_limit_usd;
  const tokens = user.input_tokens + user.output_tokens;

  const save = useCallback(async () => {
    setSaving(true);
    try {
      const trimmed = value.trim();
      const limit = trimmed === "" ? null : Number(trimmed);
      if (limit != null && (Number.isNaN(limit) || limit < 0)) {
        return; // ignore invalid; leave editor open
      }
      await api.setUserLimit(user.email, limit);
      setEditing(false);
      onChanged();
    } finally {
      setSaving(false);
    }
  }, [value, user.email, onChanged]);

  return (
    <tr className="border-t border-border-subtle/60 hover:bg-dark-panel/40" data-testid={`admin-user-row`} data-email={user.email}>
      <td className="px-3 py-2 font-mono text-text-primary">{user.email}</td>
      <td className="px-3 py-2 text-right text-text-muted font-mono text-[11px]">{formatAgo(user.last_login_at)}</td>
      <td className="px-3 py-2 text-right text-text-secondary">{user.settled}/{user.tasks}</td>
      <td className={clsx("px-3 py-2 text-right font-mono", overLimit ? "text-accent-red font-semibold" : "text-text-primary")}>
        {formatUsd(user.spent_today_usd)}
        {overLimit && <span className="ml-1 text-[10px] uppercase">over</span>}
      </td>
      <td className="px-3 py-2 text-right">
        {editing ? (
          <span className="inline-flex items-center gap-1 justify-end">
            <span className="text-text-muted">$</span>
            <input
              autoFocus
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void save(); if (e.key === "Escape") setEditing(false); }}
              placeholder="none"
              data-testid="admin-limit-input"
              className="w-20 bg-dark-primary border border-border-subtle rounded px-1.5 py-0.5 text-right text-text-primary font-mono text-[11px] focus:outline-none focus:border-accent-purple/60"
            />
            <button onClick={() => void save()} disabled={saving} aria-label="Save limit"
              className="p-0.5 text-accent-green hover:bg-accent-green/10 rounded">
              <Check size={14} strokeWidth={2.5} />
            </button>
            <button onClick={() => setEditing(false)} aria-label="Cancel"
              className="p-0.5 text-text-muted hover:text-text-primary rounded">
              <X size={14} strokeWidth={2.5} />
            </button>
          </span>
        ) : (
          <button
            onClick={() => { setValue(user.daily_limit_usd != null ? String(user.daily_limit_usd) : ""); setEditing(true); }}
            data-testid="admin-limit-edit"
            className="inline-flex items-center gap-1.5 text-text-secondary hover:text-accent-purple group"
          >
            {/* Explicit override shows the value; otherwise show the
                effective (default) limit with a hint, or — if neither. */}
            {user.daily_limit_usd != null ? (
              <span className="font-mono">{formatUsd(user.daily_limit_usd)}</span>
            ) : user.effective_limit_usd != null ? (
              <span className="font-mono text-text-muted">{formatUsd(user.effective_limit_usd)} <span className="text-[10px]">(default)</span></span>
            ) : (
              <span className="font-mono">—</span>
            )}
            <Pencil size={12} strokeWidth={2.25} className="opacity-0 group-hover:opacity-100 transition-opacity" />
          </button>
        )}
      </td>
      <td className="px-3 py-2 text-right text-text-primary font-mono">{formatUsd(user.usd)}</td>
      <td className="px-3 py-2 text-right text-text-muted">{tokens.toLocaleString()}</td>
    </tr>
  );
}


/** "3m ago" / "2h ago" / "—". Relative time for the last-login column. */
function formatAgo(ts: number | null): string {
  if (ts == null) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}


function ActivityFeed({ items }: { items: import("../types").AdminActivityItem[] }): JSX.Element {
  return (
    <div>
      <h2 className="font-display text-[11px] font-semibold uppercase tracking-[1.5px] text-text-secondary mb-2">
        Recent activity
      </h2>
      <div className="border border-border-subtle rounded-md divide-y divide-border-subtle/60" data-testid="admin-activity">
        {items.length === 0 && (
          <div className="px-3 py-6 text-center text-text-muted italic text-[12px]">no activity yet</div>
        )}
        {items.map((a, i) => (
          <div key={a.task_id ?? `login-${i}`} data-kind={a.kind}
            className="px-3 py-2 flex items-center gap-3 text-[12px] hover:bg-dark-panel/40">
            <span className="font-mono text-text-secondary w-44 shrink-0 truncate">{a.owner_email ?? "—"}</span>
            <span className={clsx(
              "text-[10px] font-mono uppercase tracking-[1px] px-1.5 py-0.5 rounded border shrink-0",
              a.kind === "login" ? "text-accent-blue border-accent-blue/40"
                : a.status === "success" ? "text-accent-green border-accent-green/40"
                : a.status === "failed" || a.status === "rejected" ? "text-accent-red border-accent-red/40"
                : "text-text-muted border-border-subtle",
            )}>{a.kind === "login" ? "login" : a.status}</span>
            <span className="text-text-muted font-mono shrink-0">{a.kind === "login" ? "auth" : (a.agent ?? "?")}</span>
            <span className="text-text-primary truncate flex-1">{a.natural_language}</span>
            <span className="text-text-muted font-mono shrink-0">{a.cost_usd != null ? formatUsd(a.cost_usd) : "—"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
