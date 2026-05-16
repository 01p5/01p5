import { useState } from "react";
import { Undo2, CheckCircle2, AlertCircle } from "lucide-react";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import type { RollbackEntry } from "../types";

/**
 * Right-sidebar section listing captured rollbacks.
 *
 * Each destructive call that lands successfully (write_file, edit_file,
 * delete_file today; delete_pod / tf_apply once those agents declare
 * snapshots) produces one row here. Clicking Undo fires the captured
 * inverse through the same gate_tools machinery — so the user
 * re-approves before the undo lands, which is enforced by the backend
 * regardless of what the UI does.
 *
 * Polls /rollback every 2s. Already-executed entries stay visible but
 * the button is greyed out; a freshly-executed entry shows the agent's
 * reply for a couple of seconds before settling into the grey state.
 */
export function RollbackPanel(): JSX.Element {
  const { data, refresh } = usePolling(() => api.listRollbacks({ k: 20 }), 2000);
  const entries = data ?? [];

  return (
    <div className="flex flex-col min-h-0">
      <header className="px-4 py-3 border-b border-border-subtle flex items-center gap-2">
        <Undo2 size={15} className="text-accent-blue" strokeWidth={2.25} />
        <h2 className="font-display text-[11px] font-semibold uppercase tracking-[1.5px] text-text-secondary">
          Rollback queue
        </h2>
        <span className="text-[10px] font-mono text-text-muted ml-auto">
          {entries.length} captured
        </span>
      </header>
      <div
        id="rollback-list"
        data-testid="rollback-list"
        className="flex-1 overflow-auto p-3 space-y-2"
      >
        {entries.length === 0 && (
          <div className="text-[11px] font-mono text-text-muted text-center py-8">
            no captured rollbacks yet — every successful destructive
            call records its inverse here
          </div>
        )}
        {entries.map((entry) => (
          <RollbackRow key={entry.rollback_id} entry={entry} onChanged={refresh} />
        ))}
      </div>
    </div>
  );
}

interface RowProps {
  entry: RollbackEntry;
  onChanged: () => void;
  // Test seam — swap the api call for a stub.
  onExecute?: typeof api.executeRollback;
}

export function RollbackRow({ entry, onChanged, onExecute }: RowProps): JSX.Element {
  const execute = onExecute ?? api.executeRollback;
  const [status, setStatus] = useState<"idle" | "executing" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const fire = async (): Promise<void> => {
    if (entry.executed) return;
    setStatus("executing");
    setError(null);
    try {
      const out = await execute(entry.rollback_id);
      setResult(typeof out.result === "string" ? out.result : JSON.stringify(out.result));
      setStatus("done");
      onChanged();
    } catch (e) {
      setStatus("error");
      setError((e as Error).message);
    }
  };

  const tone = entry.executed
    ? "border-border-subtle bg-dark-panel/60 opacity-60"
    : status === "error"
      ? "border-accent-red/40 bg-accent-red/[0.06]"
      : "border-border-subtle bg-dark-panel";

  return (
    <div
      className={`rollback-row rounded-md border px-3 py-2.5 text-[12px] font-mono space-y-1.5 transition-colors ${tone}`}
      data-rollback-id={entry.rollback_id}
      data-executed={entry.executed ? "1" : "0"}
    >
      <div className="flex items-center gap-2">
        <span className="text-text-secondary uppercase tracking-[1px] text-[10px]">
          {entry.agent} → {entry.inverse_tool}
        </span>
        {entry.executed && (
          <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-accent-green">
            <CheckCircle2 size={10} />
            executed
          </span>
        )}
      </div>
      <p className="text-text-primary text-[12px] leading-snug">
        {entry.description}
      </p>
      <div className="text-[10px] text-text-muted">
        from task <code>{entry.task_id.slice(0, 12)}</code>
        {" · forward: "}
        <code>{entry.forward_tool}</code>
      </div>
      {error && (
        <div className="flex items-start gap-1 text-[10px] text-accent-red">
          <AlertCircle size={10} className="mt-px shrink-0" />
          <span className="break-all">{error}</span>
        </div>
      )}
      {status === "done" && result && (
        <div className="text-[10px] text-accent-green break-all">
          {result.slice(0, 200)}
        </div>
      )}
      {!entry.executed && (
        <button
          onClick={() => void fire()}
          disabled={status === "executing"}
          className="rollback-undo w-full mt-1 px-2 py-1 text-[11px] uppercase tracking-[1.5px] text-accent-blue border border-accent-blue/30 rounded hover:bg-accent-blue/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label={`undo: ${entry.description}`}
        >
          {status === "executing" ? "rolling back…" : "Undo"}
        </button>
      )}
    </div>
  );
}
