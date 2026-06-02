"""StreamingAgent — like StructuralAgent but streams raw text tokens (no response_format)."""

import logging
from typing import Any, Callable, Generator, Sequence

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessageChunk
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from .models import *

logger = logging.getLogger(__name__)


class StreamingAgent:
    """Like StructuralAgent but exposes streaming. No response_format (avoids 2nd LLM call)."""

    def __init__(
        self,
        task_id: str,
        system_prompt: str,
        model: str,
        tools: Sequence[BaseTool | Callable | dict[str, Any]] = [],
        checkpointer: BaseCheckpointSaver = InMemorySaver(),
        debug: bool = False,
        use_previous_response_id: bool = False,
        recursion_limit: int = 50,
        request_extra_body: dict = {},
        agent_type: str = "default",
        agent_id: str = "default",
    ):
        self.task_id = task_id
        self.agent_id = agent_id
        self.system_prompt = system_prompt
        self.model = model
        self.checkpointer = checkpointer
        self.agent_type = agent_type
        # Cost/token accounting (mirrors StructuralAgent so cost_from_agent
        # works here too — streaming the main reply must not drop the
        # coordinator's cost from the per-user ledger).
        self._input_tokens = 0
        self._output_tokens = 0

        kwargs = {}
        if "claude" not in model:
            kwargs["output_version"] = "responses/v1"
        llm = init_chat_model(
            model,
            **kwargs,
            use_previous_response_id=use_previous_response_id,
            extra_body=request_extra_body,
        )
        self.agent: CompiledStateGraph = create_agent(
            model=llm,
            tools=tools,
            debug=debug,
            system_prompt=system_prompt,
            checkpointer=checkpointer,
            # No response_format — avoids second LLM call entirely
        ).with_config({"recursion_limit": recursion_limit})

    def _get_checkpoint_config(self) -> dict:
        return {"configurable": {"thread_id": f"{self.task_id}:{self.agent_type}:{self.agent_id}"}}

    def stream(self, user_message: str) -> Generator[str, None, str]:
        """Yields text token strings. Returns full accumulated text."""
        thread = {"messages": [{"role": "user", "content": user_message}]}
        config = self._get_checkpoint_config()
        accumulated = ""
        for chunk, _metadata in self.agent.stream(
            thread, config, stream_mode="messages"
        ):
            if isinstance(chunk, AIMessageChunk) and chunk.content:
                # chunk.content can be str or list of dicts
                if isinstance(chunk.content, str):
                    token = chunk.content
                elif isinstance(chunk.content, list):
                    token = "".join(
                        part.get("text", "")
                        for part in chunk.content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                else:
                    continue
                if token:
                    accumulated += token
                    yield token
        # Capture token usage from the final graph state (authoritative —
        # streaming chunks don't reliably carry usage_metadata).
        self._capture_usage(config)
        return accumulated

    def _capture_usage(self, config: dict) -> None:
        """Sum input/output tokens off the run's AI messages, read from the
        compiled graph state. Best-effort: a miss just leaves cost at 0."""
        try:
            graph = self.agent
            if hasattr(graph, "first"):  # unwrap with_config() RunnableBinding
                graph = graph.first
            state = graph.get_state(config)
            for m in (getattr(state, "values", {}) or {}).get("messages", []):
                meta = getattr(m, "usage_metadata", None) or {}
                self._input_tokens += int(meta.get("input_tokens", 0) or 0)
                self._output_tokens += int(meta.get("output_tokens", 0) or 0)
        except Exception:
            logger.warning("StreamingAgent usage capture failed", exc_info=True)

    def total_token_counts(self) -> tuple[int, int]:
        return (self._input_tokens, self._output_tokens)

    def total_cost_breakdown(self) -> tuple[float, dict[str, float]]:
        """(usd, breakdown) from input/output tokens × model pricing. Simplified
        vs StructuralAgent (no cache-read split / web-search) — the coordinator
        uses neither, so the input/output terms are the whole cost."""
        price = model_costs.get(self.model, {})
        inp = self._input_tokens * price.get("input", 0) / 1_000_000
        out = self._output_tokens * price.get("output", 0) / 1_000_000
        return inp + out, {
            "input": inp, "cached_input": 0.0, "input_total": inp,
            "output": out, "web_search": 0.0,
        }

    def cleanup(self) -> None:
        """Best-effort: drop the graph reference so the LLM's httpx clients
        can be reclaimed (mirrors StructuralAgent.cleanup's intent)."""
        try:
            self.agent = None  # type: ignore[assignment]
        except Exception:
            pass

    def invoke(self, user_message: str) -> str:
        """Non-streaming fallback. Returns complete text."""
        thread = {"messages": [{"role": "user", "content": user_message}]}
        config = self._get_checkpoint_config()
        response = self.agent.invoke(thread, config)
        # Extract text from the last AI message
        messages = response.get("messages", [])
        for msg in reversed(messages):
            if hasattr(msg, "type") and msg.type == "ai" and msg.content:
                if isinstance(msg.content, str):
                    return msg.content
                if isinstance(msg.content, list):
                    return "".join(
                        part.get("text", "")
                        for part in msg.content
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
        return ""
