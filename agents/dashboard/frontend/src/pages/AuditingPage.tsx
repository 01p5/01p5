import { ClipboardList } from "lucide-react";

/**
 * AUD.2 stub. Real composition (Live Activity / Approval queue /
 * Rollback queue / Audit log, each rendered as a stacked page section)
 * lands in AUD.4. Lives behind the new Auditing tab so the route
 * resolves cleanly after the Layout drops its side panels.
 */
export function AuditingPage(): JSX.Element {
  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 py-3 border-b border-border-subtle flex items-center gap-3 bg-dark-secondary/40">
        <ClipboardList size={16} className="text-accent-blue self-center" strokeWidth={2.25} />
        <h1 className="font-display text-base font-semibold text-text-primary">Auditing</h1>
        <span className="text-[11px] font-mono text-text-muted">
          live activity · approvals · rollbacks · audit log
        </span>
      </div>
      <div className="flex-1 overflow-auto px-6 py-10 text-center text-text-muted text-sm">
        Auditing surfaces will land here in AUD.4 — Live Activity,
        Approval queue, Rollback queue, Audit log, each as a stacked
        page section.
      </div>
    </section>
  );
}
