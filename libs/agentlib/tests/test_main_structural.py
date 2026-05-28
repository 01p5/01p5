"""Cover agentlib.main StructuralAgent + the cost helpers.

The LLM (init_chat_model + create_agent) is mocked so we never hit a
real provider. We focus on the constructor branches (openai vs claude,
extra_body, web_search), invoke()'s happy path, _get_checkpoint_config,
cleanup(), and the module-level cost helpers.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from agentlib.main import (
    StructuralAgent,
    get_cost_for_type,
    new_context,
)


class _Resp(BaseModel):
    summary: str = ""


# ----- helpers -----

def _build(model: str = "openai:gpt-5-mini", **kwargs) -> tuple[StructuralAgent, MagicMock, MagicMock]:
    """Construct a StructuralAgent without hitting init_chat_model or
    create_agent. Returns the agent plus the two mocks so tests can
    assert on their kwargs."""
    with patch("agentlib.main.init_chat_model") as icm, \
         patch("agentlib.main.create_agent") as ca:
        ca.return_value.with_config.return_value = MagicMock()
        sa = StructuralAgent(
            task_id="t-1",
            system_prompt="be helpful",
            response_class=_Resp,
            model=model,
            **kwargs,
        )
    return sa, icm, ca


# ----- constructor -----

def test_constructor_records_fields_and_invocation_counter():
    sa, _icm, _ca = _build(agent_type="sysadmin", agent_id="a")
    assert sa.task_id == "t-1"
    assert sa.agent_type == "sysadmin"
    assert sa.agent_id == "a"
    assert sa.system_prompt == "be helpful"
    assert sa.response_class is _Resp
    assert sa.invocation_counter == 0
    assert sa._invocation_costs == []
    assert sa._total_input_tokens == 0
    assert sa._total_output_tokens == 0


def test_constructor_openai_path_sets_use_previous_response_id():
    sa, icm, _ca = _build(model="openai:gpt-5-mini", use_previous_response_id=True)
    kw = icm.call_args.kwargs
    # The flag is OpenAI-only — the claude branch omits it.
    assert kw.get("use_previous_response_id") is True


def test_constructor_claude_path_omits_use_previous_response_id():
    sa, icm, _ca = _build(model="anthropic:claude-sonnet-4-5", use_previous_response_id=True)
    kw = icm.call_args.kwargs
    assert "use_previous_response_id" not in kw


def test_constructor_passes_request_extra_body_into_init_chat_model():
    extra = {"reasoning": {"effort": "high"}}
    sa, icm, _ca = _build(request_extra_body=extra)
    assert icm.call_args.kwargs.get("extra_body") == extra


def test_constructor_includes_web_search_tool_when_enabled_for_openai():
    sa, _icm, ca = _build(model="openai:gpt-5", enable_web_search=True)
    tools_passed = ca.call_args.kwargs["tools"]
    # The web_search tool dict is appended to the user-supplied tools.
    assert any(isinstance(t, dict) and t.get("type") == "web_search" for t in tools_passed)


def test_constructor_includes_anthropic_web_search_tool_for_claude():
    sa, _icm, ca = _build(model="anthropic:claude-sonnet-4-5", enable_web_search=True)
    tools_passed = ca.call_args.kwargs["tools"]
    # Claude's web-search uses the dated server tool spec.
    assert any(
        isinstance(t, dict) and t.get("type", "").startswith("web_search_")
        for t in tools_passed
    )


def test_constructor_no_web_search_when_disabled():
    sa, _icm, ca = _build(enable_web_search=False)
    tools_passed = ca.call_args.kwargs["tools"]
    assert tools_passed == []  # no user tools + no web-search → empty


# ----- helpers under test -----

def test_get_checkpoint_config_namespaces_thread_by_task_agent_id():
    sa, _icm, _ca = _build(agent_type="programmer", agent_id="abc")
    cfg = sa._get_checkpoint_config()
    assert cfg == {"configurable": {"thread_id": "t-1:programmer:abc"}}


def test_get_checkpoint_config_uses_ticket_scope_when_set():
    # Inside a ticket the thread is keyed by ticket (not task) so the same
    # (ticket, agent) context is reused across re-invocations.
    sa, _icm, _ca = _build(agent_type="sysadmin", agent_id="a", ticket_id="TCK")
    cfg = sa._get_checkpoint_config()
    assert cfg == {"configurable": {"thread_id": "TCK:sysadmin:a"}}


# ----- invoke -----

def _wire_agent(sa: StructuralAgent, structured) -> MagicMock:
    """Replace sa.agent with a stub whose invoke() returns a response
    dict shaped like the langgraph contract."""
    stub = MagicMock()
    response = {
        "messages": [],
        "structured_response": structured,
    }
    stub.invoke.return_value = response
    sa.agent = stub
    return stub


def test_invoke_without_budget_guard_returns_structured_response():
    sa, _icm, _ca = _build()
    resp = _Resp(summary="hello world")
    _wire_agent(sa, resp)
    out = sa.invoke("hi")
    assert out is resp
    assert sa.invocation_counter == 1


def test_invoke_increments_counter_per_call():
    sa, _icm, _ca = _build()
    _wire_agent(sa, _Resp(summary="x"))
    sa.invoke("first")
    sa.invoke("second")
    assert sa.invocation_counter == 2


def test_invoke_with_cost_returns_structured_plus_cost_tuple():
    sa, _icm, _ca = _build()
    _wire_agent(sa, _Resp(summary="x"))
    # _calculate_response_cost reaches into response shape we don't model;
    # mock it directly to keep this test small.
    with patch.object(sa, "_calculate_response_cost", return_value=(0.5, {"input": 0.5})):
        result, cost = sa.invoke_with_cost("hi")
    assert isinstance(result, _Resp)
    assert cost == (0.5, {"input": 0.5})


def test_invoke_telemetry_failure_does_not_break_caller(capsys):
    """The block that does telemetry + cost-add is wrapped in
    try/except — a crash there must not propagate."""
    sa, _icm, _ca = _build()
    _wire_agent(sa, _Resp(summary="x"))
    with patch.object(sa, "_add_cost_to_context", side_effect=RuntimeError("boom")):
        out = sa.invoke("hi")
    assert isinstance(out, _Resp)
    # The error gets printed (logger output); not propagated.
    captured = capsys.readouterr().out
    assert "Error logging" in captured or "boom" in captured


# ----- cleanup -----

def test_cleanup_is_idempotent_and_safe_after_no_invocation():
    sa, _icm, _ca = _build()
    # No agent.invoke yet — cleanup() must still not raise.
    sa.cleanup()
    # Calling twice is also safe.
    sa.cleanup()


class _FakeSaver:
    """Checkpoint saver stand-in with observable .storage."""

    def __init__(self):
        self.storage = {"k": "v"}


def test_internal_checkpointer_is_owned_and_cleared_on_cleanup():
    sa, _icm, _ca = _build()  # no checkpointer passed -> created internally
    assert sa._owns_checkpointer is True
    fake = _FakeSaver()
    sa.checkpointer = fake
    sa.cleanup()
    assert fake.storage == {}        # owned -> wiped
    assert sa.checkpointer is None


def test_injected_checkpointer_is_not_cleared_on_cleanup():
    # A shared per-(ticket, agent) saver is owned by the orchestrator;
    # cleanup must drop our reference but leave the retained context intact.
    fake = _FakeSaver()
    sa, _icm, _ca = _build(checkpointer=fake)
    assert sa._owns_checkpointer is False
    assert sa.checkpointer is fake
    sa.cleanup()
    assert fake.storage == {"k": "v"}  # injected/shared -> preserved


# ----- module-level cost helpers -----

def test_new_context_returns_fresh_dict_each_call():
    a, b = new_context(), new_context()
    assert a is not b
    # Both empty / same shape.
    assert type(a) is type(b)


def test_sum_costs_zero_when_no_invocations():
    sa, _icm, _ca = _build()
    total, breakdown = sa.total_cost_breakdown()
    assert total == 0
    assert isinstance(breakdown, dict)


def test_get_cost_for_type_returns_tuple_for_known_agent_type():
    """Even for an unknown agent type the helper returns a (float, dict)
    tuple — it doesn't raise."""
    cost, breakdown = get_cost_for_type("does-not-exist")
    assert isinstance(cost, (int, float))
    assert isinstance(breakdown, dict)


def test_total_token_counts_starts_at_zero():
    sa, _icm, _ca = _build()
    inp, out = sa.total_token_counts()
    assert inp == 0
    assert out == 0
