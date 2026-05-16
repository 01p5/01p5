"""
AgentLib - A library for creating structured AI agents using LangChain and LangGraph.

The LLM-backed pieces (StructuralAgent, StreamingAgent) require langchain>=1.0
plus olympus_telemetry. We import them lazily so the pure-python pieces
(spec, runtime, bus, orchestrator, budget) remain usable in environments
without the full LLM stack — useful for unit tests and CI.
"""

try:
    from .main import StructuralAgent, get_cost_for_type, new_context, sum_costs
    from .streaming import StreamingAgent
    _LLM_STACK_AVAILABLE = True
except ImportError as _llm_import_error:
    StructuralAgent = None  # type: ignore[assignment]
    StreamingAgent = None  # type: ignore[assignment]
    sum_costs = None  # type: ignore[assignment]
    get_cost_for_type = None  # type: ignore[assignment]
    new_context = None  # type: ignore[assignment]
    _LLM_STACK_AVAILABLE = False
    _LLM_IMPORT_ERROR = _llm_import_error

from .approval_queue import PendingApproval, QueueApprovalHook
from .approval_webhook import WebhookApprovalHook
from .budget import BudgetExceededError, BudgetGuard, BudgetState
from .bus import Bus, BusMessage, InMemoryBus, new_message
from .bus_redis import RedisStreamsBus
from .mcp import (
    MCPClient,
    MCPError,
    MCPServerConfig,
    MCPToolResult,
    MockTransport,
    StdioTransport,
    parse_command_string,
    register_mcp_tools,
    to_langchain_tool,
)
from .mcp import (
    Transport as MCPTransport,
)
from .memory import (
    EmbeddingMemoryStore,
    InMemoryMemoryStore,
    JsonlMemoryStore,
    MemoryEntry,
    MemoryStore,
    NullMemoryStore,
    render_memory_block,
)
from .models import (
    claude,
    claude4,
    claude37,
    claude45,
    claudeopus4,
    claudeopus41,
    gpt4_1,
    gpt4_1_mini,
    gpt4_1_nano,
    gpt5,
    gpt5_mini,
    gpt5_nano,
    gpt51,
    gpt52,
    model_costs,
    ollama,
    vllm_qwen3,
)
from .orchestrator import LLMRouter, ManualRouter, Orchestrator, Router
from .plan import Plan, PlanResult, PlanStep, render_prior_results, step_to_task
from .rollback import (
    InMemoryRollbackStore,
    JsonlRollbackStore,
    NullRollbackStore,
    RollbackEntry,
    RollbackPlan,
    RollbackStore,
    new_rollback_id,
    plan_to_entry,
)
from .runtime import (
    AlwaysApprove,
    AlwaysReject,
    ConsoleApprovalHook,
    InMemoryAuditLogger,
    JsonlAuditLogger,
    ToolGateError,
    gate_tools,
)
from .spec import (
    AgentContext,
    AgentResult,
    AgentSpec,
    ApprovalDecision,
    ApprovalHook,
    AuditLogger,
    CostBreakdown,
    TaskMessage,
    cost_from_agent,
)

__all__ = [
    "StructuralAgent",
    "StreamingAgent",
    "sum_costs",
    "get_cost_for_type",
    "new_context",
    "BudgetGuard",
    "BudgetExceededError",
    "BudgetState",
    "AgentSpec",
    "AgentContext",
    "AgentResult",
    "ApprovalDecision",
    "ApprovalHook",
    "AuditLogger",
    "CostBreakdown",
    "cost_from_agent",
    "TaskMessage",
    "ToolGateError",
    "gate_tools",
    "ConsoleApprovalHook",
    "WebhookApprovalHook",
    "QueueApprovalHook",
    "PendingApproval",
    "AlwaysApprove",
    "AlwaysReject",
    "JsonlAuditLogger",
    "InMemoryAuditLogger",
    "Bus",
    "BusMessage",
    "InMemoryBus",
    "RedisStreamsBus",
    "new_message",
    "Orchestrator",
    "Router",
    "ManualRouter",
    "LLMRouter",
    "MemoryStore",
    "MemoryEntry",
    "NullMemoryStore",
    "InMemoryMemoryStore",
    "JsonlMemoryStore",
    "EmbeddingMemoryStore",
    "render_memory_block",
    "RollbackPlan",
    "RollbackEntry",
    "RollbackStore",
    "NullRollbackStore",
    "InMemoryRollbackStore",
    "JsonlRollbackStore",
    "new_rollback_id",
    "plan_to_entry",
    "MCPClient",
    "MCPError",
    "MCPServerConfig",
    "MCPToolResult",
    "MCPTransport",
    "MockTransport",
    "StdioTransport",
    "to_langchain_tool",
    "register_mcp_tools",
    "parse_command_string",
    "Plan",
    "PlanStep",
    "PlanResult",
    "render_prior_results",
    "step_to_task",
    "claude",
    "claudeopus4",
    "claudeopus41",
    "claude45",
    "claude37",
    "claude4",
    "gpt51",
    "gpt52",
    "gpt5",
    "gpt5_mini",
    "gpt4_1",
    "gpt4_1_mini",
    "gpt4_1_nano",
    "gpt5_nano",
    "ollama",
    "vllm_qwen3",
    "model_costs",
]

__version__ = "0.1.0"
