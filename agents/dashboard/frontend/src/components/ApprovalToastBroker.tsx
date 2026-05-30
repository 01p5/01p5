import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { toast } from "sonner";
import { ShieldAlert, CheckCircle2, X, FileSearch } from "lucide-react";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import { Modal } from "./Modal";
import type { PendingApproval } from "../types";

/**
 * AUD.5d — global approval toast broker.
 *
 * Headless component (mounts in Layout, renders null) that polls
 * ``/approvals`` every 1.5s and fires a Sonner toast for any approval
 * id we haven't surfaced yet — UNLESS the user is currently viewing
 * the chat the approval belongs to, in which case ChatPage's inline
 * ApprovalCard (AUD.5c) handles it. The two surfaces share the same
 * data source so we never double-render; the broker just decides
 * which one to use based on ``useLocation()`` vs ``approval.ticket_id``.
 *
 * Toast UX: summary line + 3 buttons (Approve / Decline / Details).
 * Approve + Decline are one-click with a stock reason ("approved via
 * toast" / "declined via toast"); Details opens a Modal with the full
 * args + diff + a reason input for the long path.
 *
 * The toast stays open until the user acts (``duration: Infinity``).
 * If the approval is resolved out-of-band (queue page / inline card /
 * timeout), the polling sees it disappear from /approvals and we
 * dismiss the orphan toast.
 */
export function ApprovalToastBroker(): null {
  const { data: approvals } = usePolling(api.listApprovals, 1500);
  const location = useLocation();
  // approval_id -> sonner toast id, so we can dismiss when the
  // approval disappears from /approvals (resolved elsewhere).
  const liveToasts = useRef<Map<string, string | number>>(new Map());

  useEffect(() => {
    if (!approvals) return;
    const currentTicketId = currentChatTicketId(location.pathname);
    const stillPending = new Set(approvals.map((a) => a.approval_id));

    for (const a of approvals) {
      if (liveToasts.current.has(a.approval_id)) continue;
      // Inline card already covers this — skip the popup.
      if (a.ticket_id && a.ticket_id === currentTicketId) continue;

      const toastId = toast.custom(
        (id) => (
          <ApprovalToastBody
            approval={a}
            dismiss={() => toast.dismiss(id)}
          />
        ),
        { duration: Infinity },
      );
      liveToasts.current.set(a.approval_id, toastId);
    }

    // Resolved-elsewhere cleanup: dismiss + forget any toast whose
    // approval is no longer pending.
    for (const [approvalId, toastId] of liveToasts.current.entries()) {
      if (!stillPending.has(approvalId)) {
        toast.dismiss(toastId);
        liveToasts.current.delete(approvalId);
      }
    }
  }, [approvals, location.pathname]);

  return null;
}


/** Extract the ticket id segment from a /chat/{id} pathname.
 *  Returns null for /chat (no id), other routes, or trailing slashes. */
export function currentChatTicketId(pathname: string): string | null {
  if (!pathname.startsWith("/chat/")) return null;
  const tail = pathname.slice("/chat/".length);
  const segment = tail.split("/", 1)[0];
  if (!segment) return null;
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}


function ApprovalToastBody({
  approval,
  dismiss,
}: {
  approval: PendingApproval;
  dismiss: () => void;
}): JSX.Element {
  const [busy, setBusy] = useState<"approve" | "decline" | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const resolve = async (approved: boolean): Promise<void> => {
    setBusy(approved ? "approve" : "decline");
    try {
      await api.resolveApproval(approval.approval_id, {
        approved,
        reason: approved ? "approved via toast" : "declined via toast",
      });
      dismiss();
    } catch (e) {
      toast.error(`Couldn't resolve: ${(e as Error).message}`);
      setBusy(null);
    }
  };

  return (
    <div
      data-testid={`approval-toast-${approval.approval_id}`}
      className="bg-dark-panel border border-accent-yellow/40 rounded-md p-3 min-w-[320px] max-w-[420px] shadow-2xl"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <ShieldAlert size={14} className="text-accent-yellow" strokeWidth={2.25} />
        <span className="text-[10px] font-mono uppercase tracking-[1.5px] text-accent-yellow font-semibold">
          Approval · {approval.agent} → {approval.tool}
        </span>
      </div>
      <p className="text-xs text-text-primary mb-3 leading-snug line-clamp-3">
        {approval.rationale}
      </p>
      <div className="flex gap-1.5">
        <button
          onClick={() => void resolve(true)}
          disabled={busy !== null}
          className="flex items-center gap-1 px-2.5 py-1 text-[10px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <CheckCircle2 size={12} strokeWidth={2.5} />
          {busy === "approve" ? "…" : "Approve"}
        </button>
        <button
          onClick={() => void resolve(false)}
          disabled={busy !== null}
          className="flex items-center gap-1 px-2.5 py-1 text-[10px] font-mono uppercase tracking-[1.5px] text-accent-red border border-accent-red/40 hover:bg-accent-red/10 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <X size={12} strokeWidth={2.5} />
          {busy === "decline" ? "…" : "Decline"}
        </button>
        <button
          onClick={() => setDetailsOpen(true)}
          className="flex items-center gap-1 px-2.5 py-1 text-[10px] font-mono uppercase tracking-[1.5px] text-text-secondary border border-border-subtle hover:text-text-primary hover:border-text-secondary/40 rounded transition-colors"
        >
          <FileSearch size={12} strokeWidth={2.5} />
          Details
        </button>
      </div>

      <Modal open={detailsOpen} onClose={() => setDetailsOpen(false)} title="Approval details">
        <ApprovalDetailsBody
          approval={approval}
          onResolved={() => { setDetailsOpen(false); dismiss(); }}
        />
      </Modal>
    </div>
  );
}


/** Long-form details body shown when the user clicks "Details" on a
 *  toast. Same Approve/Decline buttons + a free-form reason input. */
function ApprovalDetailsBody({
  approval,
  onResolved,
}: {
  approval: PendingApproval;
  onResolved: () => void;
}): JSX.Element {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"approve" | "decline" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const resolve = async (approved: boolean): Promise<void> => {
    setBusy(approved ? "approve" : "decline");
    setErr(null);
    try {
      await api.resolveApproval(approval.approval_id, {
        approved,
        reason: reason.trim() || (approved ? "approved via toast details" : "declined via toast details"),
      });
      onResolved();
    } catch (e) {
      setErr((e as Error).message);
      setBusy(null);
    }
  };

  return (
    <div className="space-y-3 text-sm">
      <div className="font-mono text-[11px] uppercase tracking-[1.5px] text-accent-yellow">
        {approval.agent} → {approval.tool}
      </div>
      <p className="text-text-primary leading-snug">{approval.rationale}</p>
      {approval.diff && (
        <div>
          <div className="text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">diff</div>
          <pre className="font-mono text-[11px] text-text-secondary bg-dark-primary border border-border-subtle rounded p-2 overflow-auto max-h-72">
            {approval.diff}
          </pre>
        </div>
      )}
      <div>
        <div className="text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">raw args</div>
        <pre className="font-mono text-[11px] text-text-secondary bg-dark-primary border border-border-subtle rounded p-2 overflow-auto max-h-40">
          {JSON.stringify(approval.args, null, 2)}
        </pre>
      </div>
      <div>
        <label className="block text-[10px] font-mono uppercase tracking-[1.5px] text-text-secondary mb-1">
          reason (optional)
        </label>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="left blank → 'approved via toast details'"
          className="w-full bg-dark-primary border border-border-subtle rounded px-2.5 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-blue/60"
        />
      </div>
      {err && <div className="text-[12px] text-accent-red">{err}</div>}
      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={() => void resolve(false)}
          disabled={busy !== null}
          className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-red border border-accent-red/40 hover:bg-accent-red/10 rounded disabled:opacity-40"
        >
          <X size={14} strokeWidth={2.5} />
          {busy === "decline" ? "Declining…" : "Decline"}
        </button>
        <button
          onClick={() => void resolve(true)}
          disabled={busy !== null}
          className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded disabled:opacity-40"
        >
          <CheckCircle2 size={14} strokeWidth={2.5} />
          {busy === "approve" ? "Approving…" : "Approve"}
        </button>
      </div>
    </div>
  );
}
