import { NavLink, Outlet } from "react-router-dom";
import { MessageSquare, Server, Layers, ListChecks, Hammer } from "lucide-react";
import clsx from "clsx";
import { StatusDot } from "./StatusDot";
import { BusSidebar } from "./BusSidebar";
import { ApprovalsPanel } from "./ApprovalsPanel";
import { AuditPanel } from "./AuditPanel";

const TABS = [
  { to: "/chat",       label: "Chat",       icon: MessageSquare },
  { to: "/kubernetes", label: "Kubernetes", icon: Server },
  { to: "/terraform",  label: "Terraform",  icon: Layers },
  { to: "/ansible",    label: "Ansible",    icon: ListChecks },
  { to: "/programmer", label: "Programmer", icon: Hammer },
];

export function Layout(): JSX.Element {
  return (
    <div className="h-full grid grid-rows-[auto_1fr]">
      {/* Topnav */}
      <header className="bg-dark-secondary/80 backdrop-blur-xl border-b border-border-subtle">
        <div className="flex items-center h-16 px-5 gap-6">
          <div className="flex items-baseline gap-2">
            <span className="font-display text-lg font-bold text-text-primary tracking-tight">
              Olympus
            </span>
            <span className="text-[10px] uppercase tracking-[1.5px] text-text-muted font-mono">
              dev ops platform
            </span>
          </div>
          <nav className="flex gap-1 ml-2">
            {TABS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  clsx(
                    "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
                    "border border-transparent",
                    isActive
                      ? "bg-dark-panel text-text-primary border-border-subtle"
                      : "text-text-secondary hover:text-text-primary",
                  )
                }
              >
                <Icon size={16} strokeWidth={2.25} />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="flex-1" />
          <StatusDot />
        </div>
      </header>

      {/* Three-column main */}
      <main className="grid grid-cols-[280px_1fr_380px] min-h-0 overflow-hidden">
        <BusSidebar />
        <div className="min-h-0 overflow-hidden flex flex-col">
          <Outlet />
        </div>
        <aside className="bg-dark-secondary border-l border-border-subtle grid grid-rows-[1fr_1fr] min-h-0">
          <ApprovalsPanel />
          <AuditPanel />
        </aside>
      </main>
    </div>
  );
}
