"""StreamingAgent — like StructuralAgent but streams raw text tokens (no response_format)."""

from typing import Sequence, Callable, Any, Optional, Generator
import logging

from langchain.tools import BaseTool
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langchain.chat_models import init_chat_model
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.messages import AIMessageChunk

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
        for chunk, metadata in self.agent.stream(
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
        return accumulated

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
