import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, ExternalLink } from "lucide-react";


/**
 * S2.D1 — Generic "embedded sibling portal" page. Used twice:
 *   - /capabilities/slurm  → iframes /slurm/  (slurm-mgr dashboard)
 *   - /capabilities/gpu    → iframes /gpu/    (gpu-watch dashboard)
 *
 * Olympus reverse-proxies the sub-paths via OLYMPUS_PROXY_*_URL
 * env vars (chart S2.C1 + server S2.C2). Health-gated: we poll
 * `${path}healthz` first; if it returns non-200, the chart subchart
 * is disabled OR the upstream pod is down → render a nudge instead
 * of an iframe that would just show a 502 page.
 *
 * Auth: the iframe loads under Olympus's domain + session cookie,
 * so a logged-in Olympus session protects the embedded portal too
 * (the reverse proxy enforces this at the request layer).
 */
interface PortalProps {
  /** URL path the portal lives at, with trailing slash. e.g. "/slurm/" */
  path: string;
  /** Human-readable title — shown in the nudge if upstream is down. */
  title: string;
  /** Short hint for the nudge body — e.g. "slurmDashboard.enabled=true". */
  enableHint: string;
  /** Test ID prefix so each instantiation gets unique selectors. */
  testIdPrefix: string;
}


export function SubpathPortalPage({ path, title, enableHint, testIdPrefix }: PortalProps): JSX.Element {
  const [health, setHealth] = useState<"unknown" | "ok" | "down">("unknown");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${path}healthz`, { method: "GET" });
        if (!cancelled) setHealth(r.ok ? "ok" : "down");
      } catch {
        if (!cancelled) setHealth("down");
      }
    })();
    return () => { cancelled = true; };
  }, [path]);

  if (health === "unknown") {
    return (
      <section className="flex flex-col min-h-0 h-full bg-dark-primary items-center justify-center">
        <div data-testid={`${testIdPrefix}-loading`} className="text-text-muted text-[12px] font-mono">
          checking {path}healthz…
        </div>
      </section>
    );
  }

  if (health === "down") {
    return (
      <section className="flex flex-col min-h-0 h-full bg-dark-primary">
        <div className="px-6 py-3 border-b border-border-subtle bg-dark-secondary/40">
          <h1 className="font-display text-base font-semibold text-text-primary">{title}</h1>
        </div>
        <div className="flex-1 flex items-center justify-center p-6">
          <div
            data-testid={`${testIdPrefix}-down`}
            className="max-w-xl bg-accent-yellow/[0.06] border border-accent-yellow/40 rounded-md p-5 space-y-3"
          >
            <div className="flex items-center gap-2">
              <AlertCircle size={18} className="text-accent-yellow" strokeWidth={2.25} />
              <span className="font-display font-semibold text-text-primary">
                {title} portal not deployed
              </span>
            </div>
            <p className="text-[12px] text-text-secondary leading-snug">
              Olympus tried to reach <code>{path}healthz</code> but got no
              healthy response. This sibling dashboard lives in its own pod
              (set <code>{enableHint}</code> in the Helm chart values to
              enable it). Once the pod is up, this page will iframe it
              under Olympus's session cookie.
            </p>
            <Link
              to="/mcp"
              className="inline-flex items-center gap-1.5 px-3 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-blue border border-accent-blue/40 hover:bg-accent-blue/10 rounded"
            >
              <ExternalLink size={12} /> MCP integration card
            </Link>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <iframe
        src={path}
        title={title}
        data-testid={`${testIdPrefix}-iframe`}
        className="flex-1 w-full border-0"
      />
    </section>
  );
}
