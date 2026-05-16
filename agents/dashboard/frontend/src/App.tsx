import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ChatPage } from "./pages/ChatPage";
import { KubernetesPage } from "./pages/KubernetesPage";
import { TerraformPage } from "./pages/TerraformPage";
import { AnsiblePage } from "./pages/AnsiblePage";
import { ProgrammerPage } from "./pages/ProgrammerPage";

export default function App(): JSX.Element {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="kubernetes" element={<KubernetesPage />} />
        <Route path="terraform" element={<TerraformPage />} />
        <Route path="ansible" element={<AnsiblePage />} />
        <Route path="programmer" element={<ProgrammerPage />} />
        {/* Catch-all → chat */}
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Route>
    </Routes>
  );
}
