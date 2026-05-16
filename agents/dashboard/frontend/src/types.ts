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
  cost_usd?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  wall_seconds?: number | null;
  agent?: string | null;
}

export interface TelemetryTotals {
  tasks: number;
  settled: number;
  usd: number;
  input_tokens: number;
  output_tokens: number;
  wall_seconds: number;
}

export interface TelemetryAgentBucket {
  tasks: number;
  usd: number;
  input_tokens: number;
  output_tokens: number;
  wall_seconds: number;
}

export interface TelemetryRecentEntry {
  task_id: string;
  agent: string | null;
  status: string;
  submitted_at: number;
  cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  wall_seconds: number | null;
  natural_language: string;
}

export interface TelemetryResponse {
  totals: TelemetryTotals;
  by_agent: Record<string, TelemetryAgentBucket>;
  by_status: Record<string, number>;
  recent: TelemetryRecentEntry[];
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

export interface MemoryEntry {
  task_id: string;
  agent: string;
  natural_language: string;
  summary: string;
  status: "success" | "failed" | "rejected" | "cancelled";
  ts: number;
  metadata: {
    feedback?: "good" | "bad";
    correction?: string;
    wall_seconds?: number;
    total_usd?: number;
    [k: string]: unknown;
  };
}

export interface RollbackEntry {
  rollback_id: string;
  task_id: string;
  agent: string;
  forward_tool: string;
  forward_args: Record<string, unknown>;
  inverse_tool: string;
  inverse_args: Record<string, unknown>;
  description: string;
  snapshot: Record<string, unknown>;
  ts: number;
  executed: boolean;
  executed_ts: number | null;
  executed_result: string | null;
}
