import json
import logging
import traceback
from typing import Any, Callable, Optional, Sequence

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ConfigDict

# olympus_telemetry is not on PyPI yet — degrade gracefully so the
# dashboard runs without it. When the package lands, this no-op shim
# is replaced silently.
try:
    from olympus_telemetry import telemeter  # type: ignore[import-not-found]
except ImportError:
    class _NoopTelemeter:
        def event(self, *_args: Any, **_kwargs: Any) -> None: ...
    telemeter = _NoopTelemeter()

from .budget import BudgetGuard
from .models import *

logger = logging.getLogger(__name__)


def sum_costs(
    costs: list[tuple[float, dict[str, float]]],
) -> tuple[float, dict[str, float]]:
    total_cost = 0.0
    categorical_costs = {
        "input": 0.0,
        "cached_input": 0.0,
        "input_total": 0.0,
        "output": 0.0,
        "web_search": 0.0,
    }
    for cost, breakdown in costs:
        total_cost += cost
        for key in categorical_costs.keys():
            categorical_costs[key] += breakdown.get(key, 0.0)
    return total_cost, categorical_costs


agent_execution_context = {}


def new_context():
    # Return old one and reset
    global agent_execution_context
    old_context = agent_execution_context
    agent_execution_context = {}
    return old_context


def get_cost_for_type(agent_type: str) -> tuple[float, dict[str, float]]:
    if agent_type == "all":
        return sum_costs(
            [
                agent_execution_context[atype]["total_cost"]
                for atype in agent_execution_context
            ]
        )
    if agent_type not in agent_execution_context:
        return 0.0, {
            "input": 0.0,
            "cached_input": 0.0,
            "input_total": 0.0,
            "output": 0.0,
            "web_search": 0.0,
        }
    return agent_execution_context[agent_type]["total_cost"]


class StructuralAgent:
    task_id: str
    system_prompt: str
    response_class: type
    model: str
    tools: Sequence[BaseTool | Callable | dict[str, Any]]
    debug: bool
    log_conversations: bool
    conversation_save_path: str
    use_previous_response_id: bool
    recursion_limit: int
    enable_web_search: bool = False
    agent_type: str = "default"
    request_extra_body: dict = {}

    agent: CompiledStateGraph
    checkpointer: BaseCheckpointSaver
    invocation_counter: int = 0

    def __init__(
        self,
        task_id: str,
        system_prompt: str,
        response_class: type,
        model: str,
        tools: Sequence[BaseTool | Callable | dict[str, Any]] = [],
        debug: bool = False,
        log_conversations: bool = False,
        conversation_save_path: str = "./visualizer/data",
        checkpointer: BaseCheckpointSaver | None = None,
        use_previous_response_id: bool = False,
        recursion_limit: int = 50,
        enable_web_search: bool = False,
        request_extra_body: dict = {},
        agent_type: str = "default",
        agent_id: str = "default",
        budget_guard: Optional[BudgetGuard] = None,
        telemetry_task_ids: list[str] | None = None,
    ):
        if checkpointer is None:
            checkpointer = InMemorySaver()
        self.task_id = task_id
        self.agent_id = agent_id
        self.telemetry_task_ids = telemetry_task_ids or [task_id]
        self.system_prompt = system_prompt
        self.response_class = response_class
        self.model = model
        self.tools = tools
        self.debug = debug
        self.log_conversations = log_conversations
        self.conversation_save_path = conversation_save_path
        self.checkpointer = checkpointer
        self.use_previous_response_id = use_previous_response_id
        self.recursion_limit = recursion_limit
        self.enable_web_search = enable_web_search
        self.request_extra_body = request_extra_body
        self.agent_type = agent_type
        self.budget_guard = budget_guard

        if (
            "additionalProperties" not in self.response_class.model_json_schema()
            or self.response_class.model_json_schema()["additionalProperties"]
        ):
            print(
                "\x1b[33mWARNING: New structured response api may not be compatible with your response class.\x1b[0m"
            )
        web_search_tool = (
            [
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
                if "claude" in model
                else {"type": "web_search"},
            ]
            if self.enable_web_search
            else []
        )
        # use_previous_response_id is OpenAI-Responses-API specific —
        # Anthropic's SDK rejects it with TypeError.
        kwargs: dict[str, Any] = {}
        if "claude" not in self.model:
            kwargs["use_previous_response_id"] = self.use_previous_response_id
        if self.request_extra_body:
            kwargs["extra_body"] = self.request_extra_body
        llm = init_chat_model(self.model, **kwargs)

        # response_format strategy:
        #   - Raw response_class: create_agent uses ToolStrategy (no
        #     strict=True passed to bind_tools). OpenAI GPT-5+ then
        #     rejects every tool call because the auto-generated schema
        #     omits additionalProperties=false.
        #   - ProviderStrategy: create_agent calls bind_tools(strict=True)
        #     and lets the provider's native structured-output path do
        #     the schema enforcement. Works on both OpenAI and Anthropic.
        # We always wrap in ProviderStrategy.
        from langchain.agents.structured_output import ProviderStrategy

        self.agent = create_agent(
            model=llm,
            tools=self.tools + web_search_tool,
            debug=self.debug,
            system_prompt=self.system_prompt,
            checkpointer=self.checkpointer,
            response_format=ProviderStrategy(self.response_class),
        ).with_config({"recursion_limit": self.recursion_limit})

    def _get_checkpoint_config(self) -> dict:
        return {"configurable": {"thread_id": f"{self.task_id}:{self.agent_type}:{self.agent_id}"}}

    def _dump_invocation_conversation(self) -> list[dict]:
        checkpoint_data: list[BaseMessage] = (
            self.checkpointer.get(self._get_checkpoint_config())
            .get("channel_values", {})
            .get("messages", [])
        )
        msg = [{"type": "system", "content": self.system_prompt}]
        for m in checkpoint_data:
            if not isinstance(m, BaseMessage):
                # print("Skipping non-BaseMessage in checkpoint data")
                continue
            msg.append(
                {
                    "type": m.type,
                    "content": m.content,
                }
            )
        return msg

    def _save_invocation_conversation(self) -> None:
        with open(
            f"{self.conversation_save_path}/{self.task_id}_{self.invocation_counter}.json",
            "w",
        ) as f:
            json.dump(self._dump_invocation_conversation(), f, indent=4)

    def _calculate_response_cost(self, response) -> tuple[float, dict[str, float]]:
        # This currently only works for chatgpt models
        try:
            messages = response.get("messages", [])
            total_cost = 0.0
            categorical_costs = {
                "input": 0.0,
                "cached_input": 0.0,
                "input_total": 0.0,
                "output": 0.0,
                "web_search": 0.0,
            }
            last_ai_message: AIMessage = None
            for message in messages[::-1]:
                if isinstance(message, AIMessage):
                    last_ai_message = message
                    break
            if last_ai_message is None:
                return total_cost, categorical_costs
            meta_dict = last_ai_message.usage_metadata
            categorical_costs["input"] = (
                (
                    meta_dict.get("input_tokens", 0)
                    - meta_dict.get("input_token_details", {}).get("cache_read", 0)
                )
                * model_costs[self.model]["input"]
                / 1000000
            )
            categorical_costs["cached_input"] = (
                meta_dict.get("input_token_details", {}).get("cache_read", 0)
                * model_costs[self.model]["cached_input"]
                / 1000000
            )
            categorical_costs["input_total"] = (
                categorical_costs["input"] + categorical_costs["cached_input"]
            )
            categorical_costs["output"] = (
                meta_dict.get("output_tokens", 0)
                * model_costs[self.model]["output"]
                / 1000000
            )
            for ai_message in messages:
                if not isinstance(ai_message, AIMessage):
                    continue
                content = ai_message.content
                # AIMessage.content can be a plain string (chat-completions
                # API on most providers) or a list of content blocks
                # (Responses API, web-search results, etc). Skip the
                # string case — there are no tool-call blocks to count.
                if not isinstance(content, list):
                    continue
                try:
                    for content_dict in content:
                        # Each block is normally a dict but may be a
                        # plain string for text segments. Only dict
                        # blocks carry a "type" we care about.
                        if not isinstance(content_dict, dict):
                            continue
                        if content_dict.get("type", "") in (
                            "web_search_call",
                            "web_search_tool_result",
                        ):
                            categorical_costs["web_search"] += model_costs[self.model][
                                "web_search"
                            ]
                except Exception as e:
                    print(
                        f"[model.py] Error processing web search tool calls: {e}, details:",
                        traceback.format_exc(),
                    )
            total_cost = (
                sum(categorical_costs.values()) - categorical_costs["input_total"]
            )
            return total_cost, categorical_costs
        except Exception as e:
            print(
                f"Error calculating response cost: {e}, details:",
                traceback.format_exc(),
            )
            return 0.0, {
                "input": 0.0,
                "cached_input": 0.0,
                "input_total": 0.0,
                "output": 0.0,
                "web_search": 0.0,
            }

    def _ensure_agent_in_context(self) -> None:
        if self.agent_type not in agent_execution_context:
            agent_execution_context[self.agent_type] = {
                "total_cost": (0.0, {}),
                "cost_per_invocation": [],
            }

    # Keep only the last N cost entries per agent type to prevent unbounded growth
    _MAX_COST_ENTRIES = 200

    def _add_cost_to_context(self, response) -> None:
        self._ensure_agent_in_context()
        cost_obj = self._calculate_response_cost(response)
        cpi = agent_execution_context[self.agent_type]["cost_per_invocation"]
        cpi.append(cost_obj)
        # Trim to prevent unbounded memory growth in long-running processes
        if len(cpi) > self._MAX_COST_ENTRIES:
            # Preserve total by folding old entries into a running sum
            old_entries = cpi[:-self._MAX_COST_ENTRIES]
            old_total = sum_costs(old_entries)
            del cpi[:-self._MAX_COST_ENTRIES]
            # Prepend the folded sum as a single entry so total_cost stays accurate
            cpi.insert(0, old_total)
        agent_execution_context[self.agent_type]["total_cost"] = sum_costs(cpi)

    _TELEMETRY_MAX_MSG_LEN = 2000   # per-message content cap for telemetry
    _TELEMETRY_MAX_MSGS = 20        # max messages included in telemetry

    def _truncated_conversation_for_telemetry(self) -> list[dict]:
        """Return a size-limited snapshot of the conversation for telemetry."""
        conv = self._dump_invocation_conversation()
        # Keep system + last N messages, truncate long content
        system = conv[:1]
        tail = conv[1:][-self._TELEMETRY_MAX_MSGS:]
        out = []
        for msg in system + tail:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > self._TELEMETRY_MAX_MSG_LEN:
                content = content[:self._TELEMETRY_MAX_MSG_LEN] + "…[truncated]"
            out.append({"type": msg.get("type"), "content": content})
        return out

    def _send_telemetry_event(self, response) -> None:
        try:
            event_data = {
                "cost": self._calculate_response_cost(response),
                "conversation": self._truncated_conversation_for_telemetry(),
                "agent_type": self.agent_type,
                "agent_configuration": {
                    "model": self.model,
                    "debug": self.debug,
                    "request_extra_body": self.request_extra_body,
                    "recursion_limit": self.recursion_limit,
                    "enable_web_search": self.enable_web_search,
                },
            }
            for tid in self.telemetry_task_ids:
                telemeter.event(tid, "agent_response", event_data)
        except Exception as e:
            print(
                f"[agentlib.main] Error sending telemetry event: {e}, details:",
                traceback.format_exc(),
            )

    def _invoke_with_budget_retry(
        self, thread: dict, invocation_kwargs: dict
    ) -> dict:
        """Call ``self.agent.invoke()`` with reactive budget-exceeded retry.

        If no *budget_guard* is attached, behaves identically to a plain
        ``self.agent.invoke()`` call.  When a guard is present:

        1. **Proactive gate**: ``acquire()`` blocks until local budget allows.
        2. **LLM call**: the real ``invoke()``.
        3. **Reactive catch**: if the server returns ``budget_exceeded``,
           report it to the guard, backoff via ``acquire()``, then retry.
        4. On success after a server-exceeded wait, clear the flag.
        """
        config = {**self._get_checkpoint_config(), **invocation_kwargs}

        # --- no guard: simple pass-through ---
        if self.budget_guard is None:
            return self.agent.invoke(thread, config)

        # --- proactive gate ---
        self.budget_guard.acquire()

        while True:
            try:
                response = self.agent.invoke(thread, config)
                # Success — clear server-exceeded if it was set
                self.budget_guard.clear_server_exceeded()
                return response
            except Exception as exc:
                if not BudgetGuard.is_budget_error(exc):
                    raise  # not a budget error — propagate immediately

                # --- reactive: server said budget exceeded ---
                self.budget_guard.report_server_exceeded(exc)
                with self.budget_guard._lock:
                    self.budget_guard._retry_attempts += 1
                logger.warning(
                    "[agentlib] Server budget exceeded for task=%s "
                    "(retry #%d). Entering backoff.",
                    self.task_id,
                    self.budget_guard.retry_attempts,
                )
                # acquire() will block with exponential backoff until
                # either the server_exceeded flag is cleared externally
                # (release_budget) or the circuit-breaker fires.
                # NOTE: since server_exceeded is still True, acquire()
                # blocks; we rely on the backoff sleep + eventual retry
                # to discover that the server budget has been restored.
                # After the backoff sleep, clear the flag so acquire()
                # returns and we retry the LLM call.
                self.budget_guard.acquire()
                # After the backoff pause, optimistically clear the flag
                # so we can actually attempt the call again.  If the
                # server still rejects, we'll loop back here.
                self.budget_guard.clear_server_exceeded()

    def invoke(self, user_message: str, invocation_kwargs: dict = {}) -> BaseModel:
        self.invocation_counter += 1
        thread = {"messages": [{"role": "user", "content": user_message}]}
        response = self._invoke_with_budget_retry(thread, invocation_kwargs)
        try:
            if self.log_conversations:
                self._save_invocation_conversation()
            self._add_cost_to_context(response)
            self._send_telemetry_event(response)
        except Exception as e:
            print(
                f"[agentlib.main] Error logging conversation or adding cost to context or sending telemetry event: {e}, details:",
                traceback.format_exc(),
            )
        return response["structured_response"]

    def invoke_with_cost(
        self, user_message: str, invocation_kwargs: dict = {}
    ) -> tuple[BaseModel, tuple[float, dict[str, float]]]:  ## Deprecated!
        self.invocation_counter += 1
        thread = {"messages": [{"role": "user", "content": user_message}]}
        response = self._invoke_with_budget_retry(thread, invocation_kwargs)
        if self.log_conversations:
            self._save_invocation_conversation()
        return response["structured_response"], self._calculate_response_cost(response)

    def cleanup(self) -> None:
        """Break reference cycles and close HTTP clients to free RSS.

        CompiledStateGraph, InMemorySaver, and ChatOpenAI form circular
        reference chains that CPython's refcount GC cannot break.  On
        Python < 3.12 the cyclic GC also struggles when __del__ is present.

        Additionally, the ChatOpenAI LLM holds openai.OpenAI / AsyncOpenAI
        clients wrapping httpx, which hold socket buffers and SSL contexts
        in C-level allocations invisible to tracemalloc.  We must explicitly
        close those to release RSS back to the OS.

        Call this after the last invoke() to allow immediate reclamation.
        """
        # 1. Close the LLM's underlying httpx clients (the bulk of the RSS leak)
        if hasattr(self, "agent") and self.agent is not None:
            try:
                # Dig through RunnableSequence → CompiledStateGraph → nodes → LLM
                graph = self.agent
                # .with_config() wraps in RunnableSequence; unwrap to get the graph
                if hasattr(graph, "first"):
                    graph = graph.first
                if hasattr(graph, "nodes"):
                    for node in graph.nodes.values():
                        llm = getattr(node, "llm", None) or getattr(node, "model", None)
                        if llm is None and hasattr(node, "runnable"):
                            llm = getattr(node.runnable, "llm", None)
                        if llm is None:
                            continue
                        # Close sync client (openai.OpenAI → httpx.Client)
                        root = getattr(llm, "root_client", None)
                        if root is not None and hasattr(root, "_client"):
                            try:
                                root._client.close()
                            except Exception:
                                pass
                        # Close async client (openai.AsyncOpenAI → httpx.AsyncClient)
                        aroot = getattr(llm, "root_async_client", None)
                        if aroot is not None and hasattr(aroot, "_client"):
                            try:
                                aroot._client.close()
                            except Exception:
                                pass
            except Exception:
                pass
            self.agent = None
        # 2. Clear checkpoint storage (conversation history, large JSON blobs)
        if hasattr(self, "checkpointer") and self.checkpointer is not None:
            if hasattr(self.checkpointer, "storage"):
                self.checkpointer.storage.clear()
            self.checkpointer = None
        self.tools = []
        self.budget_guard = None


if __name__ == "__main__":
    # Test StructuralAgent
    class TestResponse(BaseModel):
        model_config = ConfigDict(extra="forbid")
        answer: str

    agent = StructuralAgent(
        task_id="test_agent",
        system_prompt="You are a helpful assistant.",
        response_class=TestResponse,
        model=gpt4_1_mini,
        log_conversations=True,
        conversation_save_path=".",
    )
    response = agent.invoke("What is the capital of France?")
    print(
        response
    )  # Should print a TestResponse object with the answer field filled in.
    response2 = agent.invoke("What did I ask you to do?")
    print(
        response2
    )  # Should print a TestResponse object with the answer field filled in
