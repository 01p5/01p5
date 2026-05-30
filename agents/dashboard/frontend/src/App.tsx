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
import { SlurmPortalPage } from "./pages/SlurmPortalPage";
import { GpuPortalPage } from "./pages/GpuPortalPage";
import { MCPPage } from "./pages/MCPPage";
import { TerminalPage, TerminalSessionPage } from "./pages/TerminalPage";

// Wrapper so /chat and /chat/:ticketId both render ChatPage, remounting
// (via key) when the ticket changes so its state resets cleanly.
function ChatRoute(): JSX.Element {
  const { ticketId } = useParams();
  return <ChatPage key={ticketId ?? "new"} initialTicketId={ticketId} />;
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
            <Route path="slurm" element={<SlurmPortalPage />} />
            <Route path="gpu" element={<GpuPortalPage />} />
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
          {/* Catch-all → chat */}
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      </Routes>
    </>
  );
}
