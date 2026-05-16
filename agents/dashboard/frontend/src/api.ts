// Typed thin wrapper around the dashboard's HTTP API. Everything goes
// through fetch on relative paths — vite proxies in dev, the backend
// serves the SPA + API on the same origin in prod.

import type {
  AuditRecord,
  HealthResponse,
  MCPServerCatalog,
  MCPServerSummary,
  MemoryEntry,
  PendingApproval,
  RollbackEntry,
  TaskRecord,
  TelemetryResponse,
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

export const api = {
  health: (): Promise<HealthResponse> => getJson("/healthz"),

  // Tasks
  listTasks: (): Promise<TaskRecord[]> => getJson("/tasks"),
  getTask: (id: string): Promise<TaskRecord> =>
    getJson(`/tasks/${encodeURIComponent(id)}`),
  submitTask: (natural_language: string): Promise<{ task_id: string }> =>
    postJson("/tasks", { natural_language }),

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
};
