// Typed thin wrapper around the dashboard's HTTP API. Everything goes
// through fetch on relative paths — vite proxies in dev, the backend
// serves the SPA + API on the same origin in prod.

import type {
  AuditRecord,
  HealthResponse,
  PendingApproval,
  TaskRecord,
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
};
