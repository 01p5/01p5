import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { MessageSquare, Server, Layers, ListChecks, Hammer, Plug, History, LogOut, User } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { useAuth } from "../hooks/useAuth";
import { StatusDot } from "./StatusDot";
import { BusSidebar } from "./BusSidebar";
import { ApprovalsPanel } from "./ApprovalsPanel";
import { AuditPanel } from "./AuditPanel";
import { RollbackPanel } from "./RollbackPanel";
import { TelemetryFooter } from "./TelemetryFooter";

const TABS = [
  { to: "/chat",       label: "Chat",       icon: MessageSquare },
  { to: "/sessions",   label: "Sessions",   icon: History },
  { to: "/kubernetes", label: "Kubernetes", icon: Server },
  { to: "/terraform",  label: "Terraform",  icon: Layers },
  { to: "/ansible",    label: "Ansible",    icon: ListChecks },
  { to: "/programmer", label: "Programmer", icon: Hammer },
  { to: "/mcp",        label: "MCP",        icon: Plug },
];

export function Layout(): JSX.Element {
  const { auth } = useAuth();
  const navigate = useNavigate();
  const onLogout = async () => {
    try { await api.logout(); } catch { /* ignore */ }
    navigate("/login", { replace: true });
  };
  return (
    <div className="h-full grid grid-rows-[auto_1fr_auto]">
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
          {auth.state === "authed" && (
            <div className="flex items-center gap-2">
              <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono text-text-secondary border border-border-subtle rounded">
                <User size={14} className="text-accent-green" strokeWidth={2.5} />
                <span>{auth.email}</span>
              </div>
              <button
                onClick={() => void onLogout()}
                title="Sign out"
                className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-red border border-border-subtle hover:border-accent-red/40 rounded transition-colors"
              >
                <LogOut size={16} className="text-accent-red" strokeWidth={2.5} />
                Logout
              </button>
            </div>
          )}
          <StatusDot />
        </div>
      </header>

      {/* Three-column main */}
      <main className="grid grid-cols-[280px_1fr_380px] min-h-0 overflow-hidden">
        <BusSidebar />
        <div className="min-h-0 overflow-hidden flex flex-col">
          <Outlet />
        </div>
        <aside className="bg-dark-secondary border-l border-border-subtle grid grid-rows-3 min-h-0">
          <ApprovalsPanel />
          <RollbackPanel />
          <AuditPanel />
        </aside>
      </main>
      <TelemetryFooter />
    </div>
  );
}
