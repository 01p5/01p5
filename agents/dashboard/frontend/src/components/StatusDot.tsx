import { useEffect, useState } from "react";
import clsx from "clsx";
import { api } from "../api";

type State = "connecting" | "connected" | "degraded" | "offline";

/** Glowing health dot polled every 5s — mirrors the Artemis "DB Connected" pattern. */
export function StatusDot(): JSX.Element {
  const [state, setState] = useState<State>("connecting");

  useEffect(() => {
    let cancelled = false;
    const check = async (): Promise<void> => {
      try {
        const r = await api.health();
        if (!cancelled) setState(r.ok ? "connected" : "degraded");
      } catch {
        if (!cancelled) setState("offline");
      }
    };
    void check();
    const id = setInterval(check, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const colorClass =
    state === "connected" ? "text-accent-green"
    : state === "degraded" ? "text-accent-yellow"
    : state === "offline"   ? "text-accent-red"
    : "text-text-muted";

  return (
    <div
      id="health"
      className={clsx(
        "flex items-center gap-2 text-[11px] font-mono uppercase tracking-[1.5px]",
        "px-3 py-1.5 rounded-full border border-border-subtle",
        colorClass,
      )}
    >
      <span
        className="block w-2 h-2 rounded-full bg-current"
        style={{ animation: state === "connected" ? "pulse-glow 2s ease-in-out infinite" : undefined }}
      />
      <span>{state}</span>
    </div>
  );
}
