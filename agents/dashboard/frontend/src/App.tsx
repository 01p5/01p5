import { Routes, Route, Navigate, useParams } from "react-router-dom";
import { Toaster } from "sonner";
import { Layout } from "./components/Layout";
import { RequireAuth } from "./components/RequireAuth";
import { AuditingPage } from "./pages/AuditingPage";
import { CapabilitiesPage } from "./pages/CapabilitiesPage";
import { ChatPage } from "./pages/ChatPage";
import { LoginPage } from "./pages/LoginPage";
import { SessionsPage } from "./pages/SessionsPage";
import { KubernetesPage } from "./pages/KubernetesPage";
import { TerraformPage } from "./pages/TerraformPage";
import { AnsiblePage } from "./pages/AnsiblePage";
import { HostsPage } from "./pages/HostsPage";
import { ProgrammerPage } from "./pages/ProgrammerPage";
import { HPCPage } from "./pages/HPCPage";
import { MCPPage } from "./pages/MCPPage";
import { TerminalPage, TerminalSessionPage } from "./pages/TerminalPage";
import { AdminPage } from "./pages/AdminPage";
import { useAuth } from "./hooks/useAuth";

// Wrapper so /chat and /chat/:ticketId both render ChatPage, remounting
// (via key) when the ticket changes so its state resets cleanly.
function ChatRoute(): JSX.Element {
  const { ticketId } = useParams();
  return <ChatPage key={ticketId ?? "new"} initialTicketId={ticketId} />;
}

// ADM.4 — admin-only route guard. RequireAuth has already run (parent
// route), so here auth is "authed"; redirect non-admins to /chat.
function RequireAdmin({ children }: { children: JSX.Element }): JSX.Element {
  const { auth } = useAuth();
  if (auth.state === "loading") {
    return (
      <div className="h-full flex items-center justify-center bg-dark-primary text-text-muted font-mono text-sm">
        checking…
      </div>
    );
  }
  if (auth.state !== "authed" || !auth.isAdmin) {
    return <Navigate to="/chat" replace />;
  }
  return children;
}

export default function App(): JSX.Element {
  return (
    <>
      {/* Global toast outlet — lives outside the Routes so toasts survive
          navigation. ApprovalToastBroker (mounted in Layout) fires into
          this Toaster for any new approval the user is NOT actively
          looking at in the chat transcript. */}
      <Toaster
        richColors
        position="top-right"
        theme="dark"
        toastOptions={{
          // Match the rest of the dashboard's dark-panel look.
          style: { fontFamily: "ui-monospace, monospace", fontSize: "12px" },
        }}
      />
      <Routes>
        {/* Public sign-in page — no RequireAuth wrapping. */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatRoute />} />
          <Route path="chat/:ticketId" element={<ChatRoute />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="auditing" element={<AuditingPage />} />
          {/* NAV.1: Capabilities umbrella — the 4 agent surfaces as
              sub-tabs of one top-nav entry. */}
          <Route path="capabilities" element={<CapabilitiesPage />}>
            <Route index element={<Navigate to="kubernetes" replace />} />
            <Route path="kubernetes" element={<KubernetesPage />} />
            <Route path="terraform" element={<TerraformPage />} />
            <Route path="ansible" element={<AnsiblePage />} />
            <Route path="programmer" element={<ProgrammerPage />} />
            <Route path="hpc" element={<HPCPage />} />
            {/* slurm / gpu sibling dashboards open in a new tab (reverse-
                proxied at /slurm/ and /gpu/) — see CapabilitiesPage. They are
                no longer iframed, so there are no nested routes for them. */}
          </Route>
          {/* Backwards-compat: the legacy top-level paths Navigate-
              redirect to the nested form so bookmarks + in-app links
              from before NAV.1 still resolve. */}
          <Route path="kubernetes" element={<Navigate to="/capabilities/kubernetes" replace />} />
          <Route path="terraform"  element={<Navigate to="/capabilities/terraform"  replace />} />
          <Route path="ansible"    element={<Navigate to="/capabilities/ansible"    replace />} />
          <Route path="programmer" element={<Navigate to="/capabilities/programmer" replace />} />
          <Route path="hosts" element={<HostsPage />} />
          <Route path="mcp" element={<MCPPage />} />
          <Route path="terminal" element={<TerminalPage />} />
          <Route path="terminal/:sessionId" element={<TerminalSessionPage />} />
          {/* ADM.4 — super-admin accounting. RequireAdmin redirects a
              non-admin to /chat so a deep-linked /admin URL doesn't 404
              awkwardly; the backend 403s the data endpoints regardless. */}
          <Route path="admin" element={<RequireAdmin><AdminPage /></RequireAdmin>} />
          {/* Catch-all → chat */}
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      </Routes>
    </>
  );
}
