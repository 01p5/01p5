import { NavLink, Outlet } from "react-router-dom";
import { Server, Layers, ListChecks, Hammer, Wrench, Cpu, Activity, Database, ExternalLink } from "lucide-react";
import clsx from "clsx";

const SUBTABS = [
  { to: "kubernetes", label: "Kubernetes", icon: Server },
  { to: "terraform",  label: "Terraform",  icon: Layers },
  { to: "ansible",    label: "Ansible",    icon: ListChecks },
  { to: "programmer", label: "Programmer", icon: Hammer },
  { to: "hpc",        label: "HPC",        icon: Cpu },
];

// Sibling dashboards (slurm-mgr / gpu-watch) run in their own pods and are
// reverse-proxied under /slurm/ and /gpu/. They open in a NEW TAB rather than
// embedding an iframe — cleaner than nesting a whole SPA inside the dashboard.
// Same origin, so Olympus's session cookie carries through the proxy.
const EXTERNAL_PORTALS = [
  { href: "/slurm/", label: "Slurm", icon: Database },
  { href: "/gpu/",   label: "GPU",   icon: Activity },
];

/**
 * NAV.1 — Capabilities umbrella page. The four agent-driven surfaces
 * (Kubernetes / Terraform / Ansible / Programmer) used to be separate
 * top-nav tabs; they're now sub-tabs of one "Capabilities" entry to
 * cut topnav clutter.
 *
 * Nested routing: /capabilities/{tab} renders the matching sub-page
 * via <Outlet />. /capabilities (no tab) redirects to kubernetes.
 * The legacy top-level routes (/kubernetes, /terraform, /ansible,
 * /programmer) still resolve — they Navigate-redirect to the nested
 * form so bookmarks and links from elsewhere in the app keep working.
 */
export function CapabilitiesPage(): JSX.Element {
  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 pt-6 pb-3 max-w-7xl mx-auto w-full flex items-baseline gap-4">
        <div className="flex items-baseline gap-3">
          <Wrench size={16} className="text-accent-blue self-center" strokeWidth={2.25} />
          <h1 className="font-display text-xl font-semibold text-text-primary">Capabilities</h1>
        </div>
        <div className="flex-1" />
        <nav className="flex gap-1 items-center" data-testid="capabilities-subnav">
          {SUBTABS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-2 px-3 py-1.5 rounded-md text-[12px] font-mono uppercase tracking-[1.5px] transition-colors border",
                  isActive
                    ? "bg-dark-panel text-text-primary border-border-subtle"
                    : "text-text-secondary hover:text-text-primary border-transparent",
                )
              }
            >
              <Icon size={16} strokeWidth={2.5} />
              {label}
            </NavLink>
          ))}
          <span className="w-px h-5 bg-border-subtle mx-1" aria-hidden />
          {EXTERNAL_PORTALS.map(({ href, label, icon: Icon }) => (
            <a
              key={href}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              data-testid={`portal-link-${label.toLowerCase()}`}
              title={`Open the ${label} dashboard in a new tab`}
              className="flex items-center gap-2 px-3 py-1.5 rounded-md text-[12px] font-mono uppercase tracking-[1.5px] transition-colors border border-transparent text-text-secondary hover:text-text-primary"
            >
              <Icon size={16} strokeWidth={2.5} />
              {label}
              <ExternalLink size={12} strokeWidth={2.5} className="opacity-60" />
            </a>
          ))}
        </nav>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden flex flex-col max-w-7xl mx-auto w-full">
        <Outlet />
      </div>
    </section>
  );
}
