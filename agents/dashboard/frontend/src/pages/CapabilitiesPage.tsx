import { NavLink, Outlet } from "react-router-dom";
import { Server, Layers, ListChecks, Hammer, Wrench } from "lucide-react";
import clsx from "clsx";

const SUBTABS = [
  { to: "kubernetes", label: "Kubernetes", icon: Server },
  { to: "terraform",  label: "Terraform",  icon: Layers },
  { to: "ansible",    label: "Ansible",    icon: ListChecks },
  { to: "programmer", label: "Programmer", icon: Hammer },
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
      <div className="px-6 py-3 border-b border-border-subtle bg-dark-secondary/40 flex items-baseline gap-4">
        <div className="flex items-baseline gap-3">
          <Wrench size={16} className="text-accent-blue self-center" strokeWidth={2.25} />
          <h1 className="font-display text-base font-semibold text-text-primary">Capabilities</h1>
          <span className="text-[11px] font-mono text-text-muted">
            direct agent control surfaces — kubectl · terraform · ansible · code-gen
          </span>
        </div>
        <div className="flex-1" />
        <nav className="flex gap-1" data-testid="capabilities-subnav">
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
              <Icon size={14} strokeWidth={2.25} />
              {label}
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
        <Outlet />
      </div>
    </section>
  );
}
