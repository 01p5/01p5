import { Activity } from "lucide-react";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import { formatUsd, formatSeconds } from "./CostChip";

/**
 * One-row footer at the bottom of the layout. Polls /telemetry every
 * 4s and shows rolling totals: tasks, dollars spent, total tokens,
 * wall clock. Hidden when no tasks have been submitted yet so the
 * empty-state intro still feels clean.
 */
export function TelemetryFooter(): JSX.Element | null {
  const { data } = usePolling(api.telemetry, 4000);
  if (!data) return null;
  const totals = data.totals;
  if (totals.tasks === 0) return null;

  const avgUsd = totals.settled > 0 ? totals.usd / totals.settled : 0;
  const tokenTotal = totals.input_tokens + totals.output_tokens;

  return (
    <footer
      id="telemetry-footer"
      className="border-t border-border-subtle bg-dark-secondary/60 px-5 py-1.5 flex items-center gap-4 text-[10px] font-mono text-text-muted"
    >
      <Activity size={11} strokeWidth={2.25} className="text-accent-blue" />
      <span className="uppercase tracking-[1.5px] text-text-secondary">telemetry</span>
      <span data-stat="tasks">
        {totals.settled}/{totals.tasks} tasks
      </span>
      <span data-stat="spend">
        {formatUsd(totals.usd)} spent
        {totals.settled > 1 && (
          <> · avg {formatUsd(avgUsd)}/task</>
        )}
      </span>
      <span data-stat="tokens">
        {tokenTotal.toLocaleString()} tokens
        {" "}
        ({totals.input_tokens.toLocaleString()} in / {totals.output_tokens.toLocaleString()} out)
      </span>
      <span data-stat="wall">{formatSeconds(totals.wall_seconds)} wall</span>
      <span className="ml-auto inline-flex gap-3">
        {Object.entries(data.by_agent).slice(0, 4).map(([agent, bucket]) => (
          <span key={agent} className="text-text-secondary" data-agent={agent}>
            {agent}: {bucket.tasks}× · {formatUsd(bucket.usd)}
          </span>
        ))}
      </span>
    </footer>
  );
}
