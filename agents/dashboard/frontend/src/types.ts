// Shapes mirror the JSON the dashboard backend (agents/dashboard/src/dashboard/server.py)
// returns. Kept loose where the backend itself is loose (payload, result).

export interface HealthResponse {
  ok: boolean;
}

export interface TaskRecord {
  task_id: string;
  natural_language: string;
  submitted_at: number;
  status: "pending" | "running" | "success" | "failed" | "rejected" | "cancelled";
  result_summary?: string | null;
  result_artifacts?: Record<string, unknown> | null;
  error?: string | null;
}

export interface BusEvent {
  msg_id: string;
  task_id: string;
  sender: string;
  recipient: string;
  kind:
    | "task"
    | "result"
    | "progress"
    | "log"
    | "approval_request"
    | "approval_decision";
  timestamp: number;
  payload: unknown;
  causation_id?: string | null;
}

export interface PendingApproval {
  approval_id: string;
  agent: string;
  tool: string;
  args: Record<string, unknown>;
  rationale: string;
  diff: string | null;
  requested_at: number;
}

export interface AuditRecord {
  ts: number;
  task_id: string;
  agent: string;
  tool: string;
  args: Record<string, unknown> | string;
  result: unknown;
  approved: boolean | null;
}

export interface ToolDescriptor {
  agent: string;
  name: string;
  description: string;
  args_schema: {
    type?: string;
    properties?: Record<string, ToolPropertySchema>;
    required?: string[];
  };
  destructive: boolean;
}

export interface ToolPropertySchema {
  type?: string;
  description?: string;
  default?: unknown;
  format?: string;
  enum?: string[];
}

export interface ToolInvokeResponse {
  task_id: string;
  agent: string;
  tool: string;
  result: unknown;
  error?: string;
}
