import { ShieldAlert } from "lucide-react";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import { CodeBlock } from "./CodeBlock";
import { Button } from "./Button";

/**
 * Pending approvals — right sidebar top half. Polls /approvals
 * every 1.5s. Resolve via Approve/Reject; the dashboard's window.prompt()
 * carries the reason so the Playwright E2E suite (which auto-accepts the
 * dialog) continues to work.
 */
export function ApprovalsPanel(): JSX.Element {
  const { data, refresh } = usePolling(api.listApprovals, 1500);
  const approvals = data ?? [];

  const resolve = async (id: string, approved: boolean): Promise<void> => {
    const reason = window.prompt(
      approved ? "Reason for approval:" : "Reason for rejection:",
    ) ?? "no reason given";
    try {
      await api.resolveApproval(id, { approved, reason });
    } catch (e) {
      alert(`could not resolve approval: ${(e as Error).message}`);
    }
    refresh();
  };

  return (
    <div className="flex flex-col min-h-0 border-b border-border-subtle">
      <header className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
        <ShieldAlert size={15} className="text-accent-yellow" strokeWidth={2.25} />
        <h2 className="font-display text-[11px] font-semibold uppercase tracking-[1.5px] text-text-secondary">
          Approval queue
        </h2>
        <span className="text-[10px] font-mono text-text-muted ml-auto">
          {approvals.length} pending
        </span>
      </header>
      <div id="approvals" className="flex-1 overflow-auto p-3 space-y-3">
        {approvals.length === 0 && (
          <div className="text-text-muted italic text-xs p-2">no pending approvals</div>
        )}
        {approvals.map((a) => (
          <div
            key={a.approval_id}
            className="approval border border-accent-yellow/40 rounded-md bg-accent-yellow/[0.04] p-3 space-y-2"
          >
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="font-mono text-xs text-accent-yellow">
                {a.agent} → {a.tool}
              </h3>
              <span className="text-[10px] text-text-muted">
                {new Date(a.requested_at * 1000).toLocaleTimeString()}
              </span>
            </div>
            <div
              className="text-xs text-text-primary leading-snug max-h-24 overflow-auto"
              title={a.rationale}
            >
              {a.rationale}
            </div>
            <CodeBlock text={JSON.stringify(a.args, null, 2)} maxHeight="120px" />
            {a.diff && <CodeBlock text={a.diff} language="diff" maxHeight="200px" />}
            <div className="actions flex gap-2 pt-1">
              <Button
                variant="primary"
                size="sm"
                className="approve"
                data-id={a.approval_id}
                onClick={() => resolve(a.approval_id, true)}
              >
                Approve
              </Button>
              <Button
                variant="danger"
                size="sm"
                className="reject"
                data-id={a.approval_id}
                onClick={() => resolve(a.approval_id, false)}
              >
                Reject
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
