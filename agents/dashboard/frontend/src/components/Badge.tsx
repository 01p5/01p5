import clsx from "clsx";

type Tone = "green" | "yellow" | "red" | "blue" | "orange" | "neutral";

const TONES: Record<Tone, string> = {
  green:   "bg-accent-green/15  text-accent-green  border-accent-green/30",
  yellow:  "bg-accent-yellow/15 text-accent-yellow border-accent-yellow/30",
  red:     "bg-accent-red/15    text-accent-red    border-accent-red/30",
  blue:    "bg-accent-blue/15   text-accent-blue   border-accent-blue/30",
  orange:  "bg-accent-orange/15 text-accent-orange border-accent-orange/30",
  neutral: "bg-dark-control     text-text-secondary border-border-subtle",
};

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <span
      className={clsx(
        "inline-flex items-center px-2 py-0.5 text-[11px] font-mono rounded border uppercase tracking-wider",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Common k8s pod / node status → tone mapper. */
export function k8sStatusTone(status: string): Tone {
  const s = status.toLowerCase();
  if (s === "running" || s === "ready" || s === "succeeded") return "green";
  if (s === "pending" || s === "containercreating") return "yellow";
  if (s === "failed" || s === "crashloopbackoff" || s === "error" || s === "notready") return "red";
  if (s === "terminating") return "orange";
  return "neutral";
}
