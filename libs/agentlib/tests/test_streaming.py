"""Cover agentlib.streaming.StreamingAgent.

Both stream() and invoke() are exercised against a stubbed
``create_agent`` so we never hit a real LLM endpoint. The stub
fabricates the chunk shapes the methods know how to parse
(AIMessageChunk with str or list-of-dict content for stream; AI-typed
message with str or list-of-dict content for invoke).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Lazy import — streaming.py pulls in langchain.agents at import time.
# If langchain is missing the test layer would crash; the langchain.tools
# dependency is in CI install already, so we can import unconditionally.
from langchain_core.messages import AIMessageChunk

from agentlib.streaming import StreamingAgent


def _make_streaming_agent(model: str = "openai:gpt-5-mini") -> StreamingAgent:
    """Build a StreamingAgent without actually calling init_chat_model
    or create_agent (both mocked out). We return a constructed instance
    whose .agent is a stub the test will further configure."""
    with patch("agentlib.streaming.init_chat_model"), \
         patch("agentlib.streaming.create_agent") as ca:
        # create_agent(...).with_config(...) chain → return a stub the
        # test can further configure (.stream / .invoke return values).
        configured = MagicMock()
        configured.with_config = MagicMock(return_value=configured)
        ca.return_value.with_config.return_value = configured
        sa = StreamingAgent(
            task_id="t-1",
            system_prompt="You are a test agent.",
            model=model,
            tools=[],
            agent_type="test",
            agent_id="a-1",
        )
    # Replace the wired agent with our test-controllable stub so the
    # caller can set .stream / .invoke return values per-test.
    sa.agent = configured
    return sa


def test_constructor_records_task_id_and_model():
    sa = _make_streaming_agent(model="openai:gpt-5-mini")
    assert sa.task_id == "t-1"
    assert sa.agent_type == "test"
    assert sa.agent_id == "a-1"
    assert sa.system_prompt.startswith("You are")


def test_get_checkpoint_config_namespaces_thread_by_task_and_agent():
    sa = _make_streaming_agent()
    cfg = sa._get_checkpoint_config()
    assert cfg == {"configurable": {"thread_id": "t-1:test:a-1"}}


def test_constructor_uses_responses_v1_output_for_non_claude_models():
    """Non-claude models get the `output_version=responses/v1` kwarg."""
    with patch("agentlib.streaming.init_chat_model") as icm, \
         patch("agentlib.streaming.create_agent") as ca:
        ca.return_value.with_config.return_value = MagicMock()
        StreamingAgent(task_id="t", system_prompt="p", model="openai:gpt-5-mini")
    kwargs = icm.call_args.kwargs
    assert kwargs.get("output_version") == "responses/v1"


def test_constructor_skips_responses_v1_for_claude_models():
    with patch("agentlib.streaming.init_chat_model") as icm, \
         patch("agentlib.streaming.create_agent") as ca:
        ca.return_value.with_config.return_value = MagicMock()
        StreamingAgent(task_id="t", system_prompt="p", model="anthropic:claude-sonnet-4-5")
    kwargs = icm.call_args.kwargs
    assert "output_version" not in kwargs


def test_stream_yields_text_tokens_from_str_chunks():
    sa = _make_streaming_agent()
    # Two chunks of str-typed content; expect two tokens out.
    sa.agent.stream.return_value = [
        (AIMessageChunk(content="hello "), {}),
        (AIMessageChunk(content="world"), {}),
    ]
    got = list(sa.stream("hi"))
    assert got == ["hello ", "world"]


def test_stream_yields_text_tokens_from_list_dict_chunks():
    sa = _make_streaming_agent()
    sa.agent.stream.return_value = [
        (AIMessageChunk(content=[{"type": "text", "text": "foo"}, {"type": "tool_call", "id": "x"}]), {}),
        (AIMessageChunk(content=[{"type": "text", "text": "bar"}]), {}),
    ]
    got = list(sa.stream("hi"))
    # Only the text parts are extracted; tool_call is dropped.
    assert got == ["foo", "bar"]


def test_stream_skips_empty_chunks_and_unknown_content_shapes():
    sa = _make_streaming_agent()
    sa.agent.stream.return_value = [
        (AIMessageChunk(content=""), {}),                       # empty str → skipped
        (AIMessageChunk(content=[]), {}),                       # empty list → skipped
        (AIMessageChunk(content="real"), {}),
    ]
    assert list(sa.stream("hi")) == ["real"]


def test_invoke_returns_concatenated_text_from_str_content():
    sa = _make_streaming_agent()
    ai_msg = SimpleNamespace(type="ai", content="hello there")
    human = SimpleNamespace(type="human", content="hi")
    sa.agent.invoke.return_value = {"messages": [human, ai_msg]}
    assert sa.invoke("hi") == "hello there"


def test_invoke_returns_concatenated_text_from_list_content():
    sa = _make_streaming_agent()
    ai_msg = SimpleNamespace(type="ai", content=[
        {"type": "text", "text": "alpha "},
        {"type": "text", "text": "beta"},
        {"type": "tool_call", "id": "x"},  # ignored
    ])
    sa.agent.invoke.return_value = {"messages": [ai_msg]}
    assert sa.invoke("hi") == "alpha beta"


def test_invoke_returns_empty_when_no_ai_message():
    sa = _make_streaming_agent()
    human = SimpleNamespace(type="human", content="hi")
    sa.agent.invoke.return_value = {"messages": [human]}
    assert sa.invoke("hi") == ""


def test_invoke_returns_empty_when_messages_missing():
    sa = _make_streaming_agent()
    sa.agent.invoke.return_value = {}
    assert sa.invoke("hi") == ""
