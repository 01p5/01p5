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

export interface MCPServerSummary {
  name: string;
  target_agent: string | null;
  command: string | null;
  tool_count: number;
  tools: string[];           // names only — full descriptors via /mcp/servers/{name}/tools
  destructive: string[];
  status: "connected" | "error" | string;
  error: string | null;
}

export interface MCPToolDescriptor {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface MCPServerCatalog {
  name: string;
  tools: MCPToolDescriptor[];
}

/** Shape for POST /mcp/servers. The integrator supplies one of the
 *  two transport blocks plus a per-server destructive allowlist. */
export interface AddMCPServerRequest {
  name: string;
  target_agent: string;
  transport: "stdio" | "http";
  // stdio
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  cwd?: string | null;
  // http
  url?: string;
  headers?: Record<string, string>;
  // both
  destructive?: string[];
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

// One entry in a group-chat ticket transcript (the dashboard's
// TicketEvent, projected over SSE). Actors: "human", "main", a specialist
// agent name, or an MCP server name.
export type TicketKind =
  | "human_message"
  | "agent_message"
  | "dispatch"
  | "agent_result"
  | "tool_call"
  | "approval_request"
  | "approval_decision"
  | "mcp_event";

export interface AuthStatus {
  bypass: boolean;
  google_oauth: boolean;
  email_otp: boolean;
  allowed_domains: string[];
  cookie_secure: boolean;
}

export interface MeResponse {
  authenticated: boolean;
  email?: string;
  auth: AuthStatus;
}

export interface TicketSummary {
  ticket_id: string;
  event_count: number;
  first_message: string;
  last_ts: number;
  last_actor: string;
}

export interface TicketEventDTO {
  ticket_id: string;
  actor: string;
  kind: TicketKind;
  payload: unknown;
  seq: number;
  event_id: string;
  ts: number;
  causation_id?: string | null;
  task_id?: string | null;
}

export interface PendingApproval {
  approval_id: string;
  agent: string;
  tool: string;
  args: Record<string, unknown>;
  rationale: string;
  diff: string | null;
  requested_at: number;
  /** Group-chat ticket this approval belongs to. The chat page uses
   *  this to render the approval card inline in the matching transcript;
   *  the global toast broker uses it to suppress the cross-page popup
   *  when the user is already on /chat/{ticket_id}. May be null for
   *  approvals raised outside any ticket (legacy /tasks path). */
  ticket_id: string | null;
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

export interface InventoryHost {
  id: string;
  name: string;
  address: string;
  ssh_user: string;
  ssh_port: number;
  key_id: string | null;
  groups: string[];
  vars: Record<string, string>;
  description: string;
  created_at: number;
  updated_at: number;
}

export interface InventorySshKey {
  id: string;
  name: string;
  fingerprint: string;
  created_at: number;
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
