import { useEffect, useState } from "react";
import { History, AlertCircle } from "lucide-react";
import { api } from "../api";
import type { MemoryEntry } from "../types";

/**
 * Show similar prior runs under a chat turn as compact chips.
 *
 * The chat backend doesn't return "what memory was retrieved at the
 * time" — we approximate by re-querying ``/memory?q=<turn text>&agent=<agent>``
 * once the turn settles. That's good enough to surface "you've done
 * something like this before" without bloating the bus payload.
 *
 * Chips render only when at least one prior entry matches; an empty
 * result is silent (no header, no placeholder) so the chat stays clean
 * for one-off questions.
 */
interface Props {
  taskId: string;          // skipped if this turn IS the prior run
  query: string;           // the user's text for the turn
  agent?: string;          // routed agent; filters retrieval per-agent
  // Test seam: pass in pre-fetched entries to skip the network call.
  // Components/tests that want full control over what shows can use
  // this without mocking the api module.
  entries?: MemoryEntry[];
}

export function MemoryChips({ taskId, query, agent, entries: provided }: Props): JSX.Element | null {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(provided ?? null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (provided !== undefined) return;  // controlled mode
    let cancelled = false;
    const run = async (): Promise<void> => {
      try {
        const got = await api.listMemory({ q: query, agent, k: 3 });
        if (cancelled) return;
        // Skip the turn's own entry — a freshly-written self-match
        // would be 100% similar and useless to show.
        setEntries(got.filter((e) => e.task_id !== taskId));
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };
    void run();
    return () => { cancelled = true; };
  }, [taskId, query, agent, provided]);

  if (error) {
    return (
      <div className="flex items-center gap-1.5 text-[10px] font-mono text-accent-red/80 mt-1">
        <AlertCircle size={10} />
        memory unavailable: {error}
      </div>
    );
  }
  if (!entries || entries.length === 0) return null;

  return (
    <div className="memory-chips mt-2 flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted">
        <History size={10} />
        seen before
      </span>
      {entries.map((entry) => (
        <ChipView key={entry.task_id} entry={entry} />
      ))}
    </div>
  );
}

function ChipView({ entry }: { entry: MemoryEntry }): JSX.Element {
  const tone =
    entry.metadata.feedback === "good"
      ? "border-accent-green/40 text-accent-green hover:border-accent-green/70"
      : entry.metadata.feedback === "bad"
        ? "border-accent-red/40 text-accent-red/80 hover:border-accent-red/70"
        : "border-border-subtle text-text-secondary hover:border-accent-blue/40 hover:text-text-primary";

  // Compact one-line preview; full text + correction shown on hover.
  const preview = entry.natural_language.length > 60
    ? entry.natural_language.slice(0, 60) + "…"
    : entry.natural_language;

  const tooltip = [
    `agent: ${entry.agent}`,
    `outcome: ${entry.summary}`,
    entry.metadata.correction
      ? `correction: ${entry.metadata.correction}`
      : null,
  ].filter(Boolean).join("\n");

  return (
    <span
      className={`memory-chip inline-flex items-center gap-1 px-2 py-0.5 rounded-full border bg-dark-panel/60 text-[11px] font-mono transition-colors cursor-default ${tone}`}
      title={tooltip}
      data-task-id={entry.task_id}
      data-feedback={entry.metadata.feedback ?? ""}
    >
      {preview}
    </span>
  );
}
