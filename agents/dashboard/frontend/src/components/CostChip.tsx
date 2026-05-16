import { DollarSign, Clock } from "lucide-react";

/**
 * Tiny per-turn cost summary. Shown under settled chat turns next to
 * the timestamp + task-id chip. Hides itself if both cost and wall
 * time are missing — a still-running turn shouldn't display a $0.00
 * placeholder that re-renders the moment the result lands.
 */
interface Props {
  costUsd?: number | null;
  wallSeconds?: number | null;
  inputTokens?: number | null;
  outputTokens?: number | null;
}

export function CostChip({
  costUsd,
  wallSeconds,
  inputTokens,
  outputTokens,
}: Props): JSX.Element | null {
  const hasCost = typeof costUsd === "number" && costUsd > 0;
  const hasTime = typeof wallSeconds === "number" && wallSeconds > 0;
  if (!hasCost && !hasTime) return null;

  const tokenSuffix =
    (inputTokens && inputTokens > 0) || (outputTokens && outputTokens > 0)
      ? ` · ${inputTokens ?? 0} in / ${outputTokens ?? 0} out`
      : "";

  return (
    <span
      className="cost-chip inline-flex items-center gap-1.5 text-[10px] font-mono text-text-muted"
      data-cost-usd={costUsd ?? ""}
      data-wall-seconds={wallSeconds ?? ""}
      title={`cost: ${formatUsd(costUsd ?? 0)} · wall: ${formatSeconds(wallSeconds ?? 0)}${tokenSuffix}`}
    >
      {hasCost && (
        <span className="inline-flex items-center gap-0.5">
          <DollarSign size={9} strokeWidth={2.25} />
          {formatUsd(costUsd as number)}
        </span>
      )}
      {hasTime && (
        <span className="inline-flex items-center gap-0.5">
          <Clock size={9} strokeWidth={2.25} />
          {formatSeconds(wallSeconds as number)}
        </span>
      )}
    </span>
  );
}

/** Render dollars with enough precision to surface fractions of a cent
 * — most chat turns cost between $0.0005 and $0.005, so 4 sig figs is
 * the right floor. */
export function formatUsd(usd: number): string {
  if (usd === 0) return "$0";
  if (usd >= 0.01) return `$${usd.toFixed(4)}`;
  if (usd >= 0.0001) return `$${usd.toFixed(5)}`;
  return `$${usd.toExponential(2)}`;
}

export function formatSeconds(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return `${m}m${rem}s`;
}
