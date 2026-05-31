import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
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
 *
 * S2.F3 — refresh-safe deep linking:
 *   - Route is `slurm/*` / `gpu/*` (wildcard). Parent URL is
 *     /capabilities/slurm/<inner> and is passed through to the
 *     iframe's initial src as `${path}<inner>`. Hard refresh
 *     restores the user's place inside the iframe.
 *   - The embedded SPA posts {type:"embedded-nav", path:"/slurm/foo"}
 *     to the parent on every internal navigation; we listen and call
 *     navigate(replace:true) so the parent URL bar always matches
 *     what's actually visible in the iframe.
 *   - iframe src is set ONCE on mount (useState init). We never
 *     re-set it from prop changes — that would reload the iframe.
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
  /** Olympus's outer route, e.g. "/capabilities/slurm" — used to
   *  mirror inner-iframe nav events back into the parent URL bar. */
  parentRoute: string;
}


export function SubpathPortalPage({ path, title, enableHint, testIdPrefix, parentRoute }: PortalProps): JSX.Element {
  const [health, setHealth] = useState<"unknown" | "ok" | "down">("unknown");
  // Wildcard segment: when the route is "slurm/*" and the URL is
  // /capabilities/slurm/jobs, params["*"] is "jobs".
  const params = useParams();
  const innerPath = params["*"] ?? "";
  // Compute iframe src once on mount — recomputing on subsequent
  // navigations would force a reload and wipe the iframe's state.
  const [initialSrc] = useState(() => `${path}${innerPath}`);

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

  // Listen for navigation events from the embedded SPA. Each one
  // becomes a `replace` (not `push`) so the back button still does
  // what the user expects — one back = leave the portal, not undo
  // a single in-portal click.
  const navigate = useNavigate();
  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (e.origin !== window.location.origin) return;
      if (!e.data || typeof e.data !== "object") return;
      if (e.data.type !== "embedded-nav") return;
      const childPath = typeof e.data.path === "string" ? e.data.path : "";
      if (!childPath.startsWith(path)) return;          // not our iframe
      const inner = childPath.slice(path.length);
      const target = `${parentRoute}${inner ? "/" + inner : ""}`;
      // Avoid a redundant navigate that would itself fire another
      // postMessage round-trip.
      if (window.location.pathname !== target) {
        navigate(target, { replace: true });
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [navigate, path, parentRoute]);

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
        src={initialSrc}
        title={title}
        data-testid={`${testIdPrefix}-iframe`}
        className="flex-1 w-full border-0"
      />
    </section>
  );
}
