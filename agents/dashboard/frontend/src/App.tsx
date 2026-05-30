import { Routes, Route, Navigate, useParams } from "react-router-dom";
import { Layout } from "./components/Layout";
import { RequireAuth } from "./components/RequireAuth";
import { ChatPage } from "./pages/ChatPage";
import { LoginPage } from "./pages/LoginPage";
import { SessionsPage } from "./pages/SessionsPage";
import { KubernetesPage } from "./pages/KubernetesPage";
import { TerraformPage } from "./pages/TerraformPage";
import { AnsiblePage } from "./pages/AnsiblePage";
import { HostsPage } from "./pages/HostsPage";
import { ProgrammerPage } from "./pages/ProgrammerPage";
import { MCPPage } from "./pages/MCPPage";

// Wrapper so /chat and /chat/:ticketId both render ChatPage, remounting
// (via key) when the ticket changes so its state resets cleanly.
function ChatRoute(): JSX.Element {
  const { ticketId } = useParams();
  return <ChatPage key={ticketId ?? "new"} initialTicketId={ticketId} />;
}

export default function App(): JSX.Element {
  return (
    <Routes>
      {/* Public sign-in page — no RequireAuth wrapping. */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="chat" element={<ChatRoute />} />
        <Route path="chat/:ticketId" element={<ChatRoute />} />
        <Route path="sessions" element={<SessionsPage />} />
        <Route path="kubernetes" element={<KubernetesPage />} />
        <Route path="terraform" element={<TerraformPage />} />
        <Route path="ansible" element={<AnsiblePage />} />
        <Route path="hosts" element={<HostsPage />} />
        <Route path="programmer" element={<ProgrammerPage />} />
        <Route path="mcp" element={<MCPPage />} />
        {/* Catch-all → chat */}
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}
