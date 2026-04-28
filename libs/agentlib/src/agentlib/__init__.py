"""
AgentLib - A library for creating structured AI agents using LangChain and LangGraph.
"""

from .main import StructuralAgent, sum_costs, get_cost_for_type, new_context
from .streaming import StreamingAgent
from .budget import BudgetGuard, BudgetExceededError, BudgetState
from .models import (
    claude,
    claudeopus4,
    claudeopus41,
    claude45,
    claude37,
    claude4,
    gpt51,
    gpt52,
    gpt5,
    gpt5_mini,
    gpt4_1,
    gpt4_1_mini,
    gpt4_1_nano,
    gpt5_nano,
    ollama,
    vllm_qwen3,
    model_costs,
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
