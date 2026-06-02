import { useState } from "react";
import { ScrollText } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import { Modal } from "./Modal";
import type { AuditRecord } from "../types";

const APPROVED_CLASS: Record<string, string> = {
  true: "approved-true text-accent-green",
  false: "approved-false text-accent-red",
  null: "approved-null text-text-muted",
};

const fmtArgs = (args: unknown): string => {
  if (typeof args === "string") return args;
  try { return JSON.stringify(args); } catch { return String(args); }
};

/** Pretty multi-line JSON for the detail modal. */
const prettyJson = (v: unknown): string => {
  if (v === null || v === undefined || v === "") return "(empty)";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
};

/**
 * Audit log — polls /audit (JSONL) every 3s. Records that mutated state
 * (approved=true/false) get a tone; read-only tool calls (approved=null)
 * are dimmer. Rows are clickable: a row only shows truncated args, so a
 * click opens the full record (args + result + ids) in a modal.
 */
export function AuditPanel(): JSX.Element {
  const { data } = usePolling(api.audit, 3000);
  const rows = (data ?? []).slice(-100).reverse();
  const [selected, setSelected] = useState<AuditRecord | null>(null);

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
            <button
              key={`${rec.ts}:${i}`}
              type="button"
              onClick={() => setSelected(rec)}
              title="Click for the full record (args + result)"
              className="audit-row w-full text-left font-mono text-[11px] leading-snug py-1 px-2 hover:bg-dark-tertiary/40 hover:border-accent-blue/30 rounded border-b border-border-subtle/40 cursor-pointer transition-colors"
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
            </button>
          );
        })}
      </div>

      <AuditDetailModal record={selected} onClose={() => setSelected(null)} />
    </div>
  );
}


/** Full audit record — the ids, routing, exact time, and the args +
 *  result the truncated row can't show. */
function AuditDetailModal({ record, onClose }: {
  record: AuditRecord | null;
  onClose: () => void;
}): JSX.Element {
  const approvedLabel = record == null
    ? ""
    : record.approved === null ? "read-only (not gated)"
      : record.approved ? "approved" : "rejected";
  const approvedCls = record == null
    ? ""
    : APPROVED_CLASS[String(record.approved)] ?? APPROVED_CLASS.null;

  return (
    <Modal open={record !== null} onClose={onClose} title="Audit record">
      {record && (
        <div className="space-y-4" data-testid="audit-detail">
          <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-[12px]">
            <Field label="agent">{record.agent}</Field>
            <Field label="tool"><span className="text-accent-blue font-mono">{record.tool}</span></Field>
            <Field label="approved"><span className={clsx("font-mono", approvedCls)}>{approvedLabel}</span></Field>
            <Field label="time">{new Date(record.ts * 1000).toLocaleString()}</Field>
            <Field label="task_id"><code className="break-all">{record.task_id}</code></Field>
          </dl>
          <Section title="args">{prettyJson(record.args)}</Section>
          <Section title="result">{prettyJson(record.result)}</Section>
        </div>
      )}
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <>
      <dt className="font-mono text-[10px] uppercase tracking-[1px] text-text-muted pt-0.5">{label}</dt>
      <dd className="text-text-primary font-mono break-words">{children}</dd>
    </>
  );
}

function Section({ title, children }: { title: string; children: string }): JSX.Element {
  return (
    <div>
      <div className="text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">{title}</div>
      <pre className="font-mono text-[11px] leading-snug text-text-primary bg-dark-primary border border-border-subtle rounded p-3 overflow-auto max-h-[40vh] whitespace-pre-wrap break-words">
        {children}
      </pre>
    </div>
  );
}
