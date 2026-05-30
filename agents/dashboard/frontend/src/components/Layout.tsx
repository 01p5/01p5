import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { MessageSquare, Plug, Network, ClipboardList, Wrench, TerminalSquare } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { useAuth } from "../hooks/useAuth";
import { ApprovalToastBroker } from "./ApprovalToastBroker";
import { StatusDot } from "./StatusDot";
import { TelemetryFooter } from "./TelemetryFooter";

// CHAT.1 dropped Sessions (it lives in SessionsRail inside ChatPage).
// NAV.1 collapsed Kubernetes / Terraform / Ansible / Programmer into
// one "Capabilities" entry (sub-tabs inside CapabilitiesPage). The
// legacy /sessions, /kubernetes, /terraform, /ansible, /programmer
// routes stay registered in App.tsx so deep-links keep working.
const TABS = [
  { to: "/chat",         label: "Chat",         icon: MessageSquare },
  { to: "/auditing",     label: "Auditing",     icon: ClipboardList },
  { to: "/capabilities", label: "Capabilities", icon: Wrench },
  { to: "/hosts",        label: "Hosts",        icon: Network },
  { to: "/terminal",     label: "Terminal",     icon: TerminalSquare },
  { to: "/mcp",          label: "MCP",          icon: Plug },
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
              <div className="hidden sm:flex items-center px-2.5 py-1 text-[11px] font-mono text-text-secondary border border-border-subtle rounded">
                <span>{auth.email}</span>
              </div>
              <button
                onClick={() => void onLogout()}
                title="Sign out"
                className="px-2.5 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-red border border-border-subtle hover:border-accent-red/40 rounded transition-colors"
              >
                Logout
              </button>
            </div>
          )}
          <StatusDot />
        </div>
      </header>

      {/* Single-column main. Live Activity / Approval queue / Rollback
          queue / Audit log used to live in left + right side panels
          here; they moved to /auditing (AUD.2/4). New approvals now
          surface as a global Sonner toast — except when the user is
          looking at the chat ticket the approval belongs to, where the
          approval card renders inline in the transcript. */}
      <main className="min-h-0 overflow-hidden flex flex-col">
        <Outlet />
      </main>
      <TelemetryFooter />
      <ApprovalToastBroker />
    </div>
  );
}
