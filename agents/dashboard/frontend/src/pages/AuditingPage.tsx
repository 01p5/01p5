import { ClipboardList } from "lucide-react";
import { ApprovalsPanel } from "../components/ApprovalsPanel";
import { AuditPanel } from "../components/AuditPanel";
import { BusSidebar } from "../components/BusSidebar";
import { RollbackPanel } from "../components/RollbackPanel";

/**
 * Auditing — single page that hosts the four observability surfaces
 * that used to ride along as always-visible side panels (Layout's
 * BusSidebar / ApprovalsPanel / RollbackPanel / AuditPanel).
 *
 * Each surface gets its own scroll-capped card so one busy stream
 * (the bus, or a long audit log) can't crowd out the others. The
 * components themselves are unchanged — they already use ``flex-col
 * min-h-0`` + ``flex-1 overflow-auto``, which means a wrapper with a
 * finite height plus ``flex-col`` is all they need to scroll
 * internally.
 *
 * New approvals fire a Sonner toast globally (ApprovalToastBroker in
 * the Layout) — the Approval queue panel here is the deep-dive view
 * for when the user wants to see everything pending at once.
 */
export function AuditingPage(): JSX.Element {
  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 pt-6 pb-3 max-w-7xl mx-auto w-full flex items-center gap-3">
        <ClipboardList size={16} className="text-accent-blue self-center" strokeWidth={2.25} />
        <h1 className="font-display text-xl font-semibold text-text-primary">Auditing</h1>
        <span className="text-[11px] font-mono text-text-muted">
          live activity · approvals · rollbacks · audit log
        </span>
      </div>

      <div className="flex-1 overflow-auto px-6 py-6 max-w-7xl mx-auto w-full space-y-5">
        <SectionCard testId="section-live-activity">
          <BusSidebar />
        </SectionCard>

        <SectionCard testId="section-approvals">
          <ApprovalsPanel />
        </SectionCard>

        <SectionCard testId="section-rollback">
          <RollbackPanel />
        </SectionCard>

        <SectionCard testId="section-audit-log">
          <AuditPanel />
        </SectionCard>
      </div>
    </section>
  );
}


/**
 * Fixed-height wrapper card. Constrains a panel so its internal
 * ``flex-1 overflow-auto`` actually scrolls — without this, the panel
 * would expand to fit all rows and the page would scroll instead, which
 * defeats the per-section feel.
 */
function SectionCard({ children, testId }: {
  children: React.ReactNode;
  testId: string;
}): JSX.Element {
  return (
    <div
      data-testid={testId}
      className="h-[360px] flex flex-col bg-dark-panel border border-border-subtle rounded-lg overflow-hidden"
    >
      {children}
    </div>
  );
}
