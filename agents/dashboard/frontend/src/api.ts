// Typed thin wrapper around the dashboard's HTTP API. Everything goes
// through fetch on relative paths — vite proxies in dev, the backend
// serves the SPA + API on the same origin in prod.

import type {
  AddMCPServerRequest,
  AuditRecord,
  HealthResponse,
  InventoryHost,
  InventorySshKey,
  MCPServerCatalog,
  MCPServerSummary,
  MemoryEntry,
  PendingApproval,
  RollbackEntry,
  MeResponse,
  TaskRecord,
  TelemetryResponse,
  TerminalSession,
  TicketEventDTO,
  TicketSummary,
  ToolDescriptor,
  ToolInvokeResponse,
} from "./types";

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = "";
    try {
      const body = (await r.json()) as { error?: string };
      detail = body?.error ?? "";
    } catch { /* ignore */ }
    throw new Error(`${r.status} ${r.statusText}${detail ? `: ${detail}` : ""}`);
  }
  return (await r.json()) as T;
}

async function getJson<T>(url: string): Promise<T> {
  return jsonOrThrow<T>(await fetch(url));
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  return jsonOrThrow<T>(
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

async function putJson<T>(url: string, body: unknown): Promise<T> {
  return jsonOrThrow<T>(
    await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

async function deleteJson<T>(url: string): Promise<T> {
  return jsonOrThrow<T>(await fetch(url, { method: "DELETE" }));
}

export const api = {
  health: (): Promise<HealthResponse> => getJson("/healthz"),

  // Auth — /me returns the authenticated user or 401 (RequireAuth treats
  // the 401 as "redirect to /login"); the email-OTP routes return
  // structured errors (rate_limited / email_not_allowed) so the UI can
  // surface them.
  me: async (): Promise<MeResponse> => {
    const r = await fetch("/me");
    if (r.status === 401 || r.status === 200) {
      return (await r.json()) as MeResponse;
    }
    throw new Error(`${r.status} ${r.statusText}`);
  },
  logout: (): Promise<{ ok: true }> => postJson("/auth/logout", {}),
  googleStartUrl: (): string => "/auth/google/start",
  emailStart: (email: string): Promise<{ sent: true; email: string }> =>
    postJson("/auth/email/start", { email }),
  emailVerify: (email: string, code: string): Promise<{ authenticated: true; email: string }> =>
    postJson("/auth/email/verify", { email, code }),

  // Tasks
  listTasks: (): Promise<TaskRecord[]> => getJson("/tasks"),
  getTask: (id: string): Promise<TaskRecord> =>
    getJson(`/tasks/${encodeURIComponent(id)}`),
  submitTask: (natural_language: string): Promise<{ task_id: string }> =>
    postJson("/tasks", { natural_language }),

  // Group-chat tickets — the chat session IS the ticket. Posting a
  // message runs the main agent on the ticket; the transcript (human +
  // agent messages, dispatches, tool calls, ask_agent exchanges) streams
  // back over GET /tickets/{id}/events.
  sendTicketMessage: (
    ticketId: string,
    message: string,
  ): Promise<{ ticket_id: string }> =>
    postJson(`/tickets/${encodeURIComponent(ticketId)}/messages`, { message }),
  listTickets: async (): Promise<TicketSummary[]> => {
    const body = await getJson<{ tickets: TicketSummary[] }>("/tickets");
    return body.tickets;
  },
  getTicket: (
    ticketId: string,
  ): Promise<{ ticket_id: string; events: TicketEventDTO[] }> =>
    getJson(`/tickets/${encodeURIComponent(ticketId)}`),
  closeTicket: (
    ticketId: string,
  ): Promise<{ ticket_id: string; summary: string }> =>
    postJson(`/tickets/${encodeURIComponent(ticketId)}/close`, {}),

  // Approvals
  listApprovals: (): Promise<PendingApproval[]> => getJson("/approvals"),
  resolveApproval: (
    id: string,
    body: { approved: boolean; reason: string; modified_args?: Record<string, unknown> },
  ): Promise<{ resolved: string }> =>
    postJson(`/approvals/${encodeURIComponent(id)}`, body),

  // Audit log: newline-delimited JSON, last 100 entries by convention
  audit: async (): Promise<AuditRecord[]> => {
    const r = await fetch("/audit");
    if (!r.ok) return [];
    const text = await r.text();
    return text
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((line) => {
        try { return JSON.parse(line) as AuditRecord; } catch { return null; }
      })
      .filter((x): x is AuditRecord => x !== null);
  },

  // Tools catalog + direct invocation
  listTools: (): Promise<ToolDescriptor[]> => getJson("/tools"),
  invokeTool: (
    agent: string,
    tool: string,
    args: Record<string, unknown>,
  ): Promise<ToolInvokeResponse> =>
    postJson(`/tools/${encodeURIComponent(agent)}/${encodeURIComponent(tool)}`, args),

  // Infra catalog
  terraformStacks: (): Promise<string[]> => getJson("/stacks/terraform"),
  ansiblePlaybooks: (): Promise<string[]> => getJson("/stacks/ansible"),

  // Memory (retrieval + feedback)
  listMemory: async (params?: {
    q?: string;
    agent?: string;
    k?: number;
  }): Promise<MemoryEntry[]> => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set("q", params.q);
    if (params?.agent) qs.set("agent", params.agent);
    if (params?.k) qs.set("k", String(params.k));
    const path = qs.toString() ? `/memory?${qs.toString()}` : "/memory";
    const body = await getJson<{ entries: MemoryEntry[] }>(path);
    return body.entries;
  },
  memoryFeedback: (
    taskId: string,
    body: { feedback?: "good" | "bad" | null; correction?: string | null },
  ): Promise<{ updated: true; task_id: string }> =>
    postJson(`/memory/${encodeURIComponent(taskId)}/feedback`, body),

  // Rollback (list + execute the captured inverse)
  listRollbacks: async (params?: {
    task_id?: string;
    k?: number;
  }): Promise<RollbackEntry[]> => {
    const qs = new URLSearchParams();
    if (params?.task_id) qs.set("task_id", params.task_id);
    if (params?.k) qs.set("k", String(params.k));
    const path = qs.toString() ? `/rollback?${qs.toString()}` : "/rollback";
    const body = await getJson<{ entries: RollbackEntry[] }>(path);
    return body.entries;
  },
  executeRollback: (
    rollbackId: string,
  ): Promise<{
    rollback_id: string;
    task_id: string;
    agent: string;
    tool: string;
    result: unknown;
  }> => postJson(`/rollback/${encodeURIComponent(rollbackId)}/execute`, {}),

  // Telemetry — rolled-up cost + tokens across stored task records.
  telemetry: (): Promise<TelemetryResponse> => getJson("/telemetry"),

  // MCP (Model Context Protocol) — third-party tool servers wired in
  // at dashboard startup. Read-only for v1; runtime add/remove is a
  // follow-up.
  listMcpServers: async (): Promise<MCPServerSummary[]> => {
    const body = await getJson<{ servers: MCPServerSummary[] }>("/mcp/servers");
    return body.servers;
  },
  getMcpServerTools: (name: string): Promise<MCPServerCatalog> =>
    getJson(`/mcp/servers/${encodeURIComponent(name)}/tools`),

  /** Register a new MCP server at runtime. The backend immediately
   *  attempts to connect + list its tools; failures land as a server
   *  card with status="error" rather than a thrown exception. */
  addMcpServer: (req: AddMCPServerRequest): Promise<MCPServerSummary> =>
    postJson("/mcp/servers", req),

  /** Disconnect a registered MCP server: strips its tools off the
   *  target agent + closes the transport. 404 if no such server. */
  deleteMcpServer: (name: string): Promise<{ removed: true; name: string }> =>
    deleteJson(`/mcp/servers/${encodeURIComponent(name)}`),

  // ---- Inventory (Phase INV) — user-managed hosts + ssh keys.
  // Keys are write-only via the API: list returns id+name+fingerprint;
  // the PEM body is paste-once and never returned.
  listHosts: async (): Promise<InventoryHost[]> => {
    const body = await getJson<{ hosts: InventoryHost[] }>("/inventory/hosts");
    return body.hosts;
  },
  addHost: async (body: {
    name: string;
    address: string;
    ssh_user?: string;
    ssh_port?: number;
    key_id?: string | null;
    groups?: string[];
    vars?: Record<string, string>;
    description?: string;
  }): Promise<InventoryHost> => {
    const r = await postJson<{ host: InventoryHost }>("/inventory/hosts", body);
    return r.host;
  },
  updateHost: async (id: string, patch: Partial<{
    name: string;
    address: string;
    ssh_user: string;
    ssh_port: number;
    key_id: string | null;
    groups: string[];
    vars: Record<string, string>;
    description: string;
  }>): Promise<InventoryHost> => {
    const r = await putJson<{ host: InventoryHost }>(
      `/inventory/hosts/${encodeURIComponent(id)}`, patch,
    );
    return r.host;
  },
  removeHost: (id: string): Promise<{ ok: true }> =>
    deleteJson(`/inventory/hosts/${encodeURIComponent(id)}`),

  listKeys: async (): Promise<InventorySshKey[]> => {
    const body = await getJson<{ keys: InventorySshKey[] }>("/inventory/keys");
    return body.keys;
  },
  addKey: async (name: string, content: string): Promise<InventorySshKey> => {
    const r = await postJson<{ key: InventorySshKey }>("/inventory/keys",
      { name, content });
    return r.key;
  },
  removeKey: (id: string): Promise<{ ok: true }> =>
    deleteJson(`/inventory/keys/${encodeURIComponent(id)}`),

  /** Plain-text ansible inventory preview (text/plain, not JSON). */
  renderInventory: async (): Promise<string> => {
    const r = await fetch("/inventory/render");
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.text();
  },

  // ---- Terminal sessions (TERM.2a/2b backend, TERM.3 UI) ----
  // Pool of operator-driven SSH-PTY sessions hosted on the dashboard
  // pod. Create picks an inventory host (+ optional ssh_user override);
  // the server returns a ws_url the browser dials with xterm.js.
  listTerminalSessions: async (): Promise<TerminalSession[]> => {
    const r = await getJson<{ sessions: TerminalSession[] }>("/terminal/sessions");
    return r.sessions;
  },
  createTerminalSession: async (body:
    | { host_alias: string; ssh_user?: string }   // ssh mode (default)
    | { kind: string }                            // local CLI mode (e.g. "olympus-tui")
  ): Promise<TerminalSession> => {
    const r = await postJson<{ session: TerminalSession }>(
      "/terminal/sessions", body,
    );
    return r.session;
  },
  removeTerminalSession: (id: string): Promise<{ ok: true }> =>
    deleteJson(`/terminal/sessions/${encodeURIComponent(id)}`),

  /** Pull-based ask of the terminal_companion agent (TERM.4b).
   *  Returns the structured answer + any suggested next-command
   *  one-liners the LLM produced. The frontend renders them as code
   *  blocks the user can copy by hand (click-to-inject lands in
   *  TERM.5). */
  askTerminalCompanion: (sessionId: string, question: string): Promise<{
    answer: string;
    suggested_commands: string[];
  }> => postJson(
    `/terminal/sessions/${encodeURIComponent(sessionId)}/ask`,
    { question },
  ),
};
