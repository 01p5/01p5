import { ScrollText } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";

const APPROVED_CLASS: Record<string, string> = {
  true: "approved-true text-accent-green",
  false: "approved-false text-accent-red",
  null: "approved-null text-text-muted",
};

/**
 * Audit log — right sidebar bottom half. Polls /audit (JSONL) every 3s.
 * Records that mutated state (approved=true/false) get a tone; read-only
 * tool calls (approved=null) are dimmer.
 */
export function AuditPanel(): JSX.Element {
  const { data } = usePolling(api.audit, 3000);
  const rows = (data ?? []).slice(-100).reverse();

  const fmtArgs = (args: unknown): string => {
    if (typeof args === "string") return args;
    try { return JSON.stringify(args); } catch { return String(args); }
  };

  return (
    <div className="flex flex-col min-h-0">
      <header className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
        <ScrollText size={15} className="text-text-secondary" strokeWidth={2.25} />
        <h2 className="font-display text-[11px] font-semibold uppercase tracking-[1.5px] text-text-secondary">
          Audit log
        </h2>
        <span className="text-[10px] font-mono text-text-muted ml-auto">
          {rows.length}
        </span>
      </header>
      <div id="audit" className="flex-1 overflow-auto p-3 space-y-px">
        {rows.length === 0 && (
          <div className="text-text-muted italic text-xs p-2">no audit entries yet</div>
        )}
        {rows.map((rec, i) => {
          const cls = APPROVED_CLASS[String(rec.approved)] ?? APPROVED_CLASS.null;
          const ts = new Date(rec.ts * 1000).toLocaleTimeString();
          return (
            <div
              key={`${rec.ts}:${i}`}
              className="audit-row font-mono text-[11px] leading-snug py-1 px-2 hover:bg-dark-tertiary/40 rounded border-b border-border-subtle/40"
            >
              <span className={clsx(cls)}>
                [{rec.approved === null ? "—" : String(rec.approved)}]
              </span>{" "}
              <span className="text-text-muted">{ts}</span>{" "}
              <span className="text-text-primary">{rec.agent}</span>{" "}
              <span className="text-accent-blue">{rec.tool}</span>{" "}
              <span className="text-text-muted truncate inline-block max-w-[200px] align-bottom">
                {fmtArgs(rec.args)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
