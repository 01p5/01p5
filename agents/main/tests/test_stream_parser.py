"""_StreamParser — peels the coordinator's streamed JSON objects into
interleaved thinking steps + a clean final reply. No LLM. This is the guard
against the old failure mode where raw JSON leaked into the chat UI.
"""
from __future__ import annotations

from main_agent.agent import _StreamParser


def _run(parser: _StreamParser, text: str, chunk: int = 7) -> None:
    """Feed `text` in small chunks to exercise arbitrary token boundaries."""
    for i in range(0, len(text), chunk):
        parser.feed(text[i : i + chunk])
    parser.close()


def test_compact_jsonl_split_across_chunks():
    seen: list[str] = []
    p = _StreamParser(seen.append)
    _run(
        p,
        '{"thinking": "looking at nodes"}\n'
        '{"thinking":"dispatching sysadmin"}\n'
        '{"reply": "Found 2 nodes.", "resolved": true}',
    )
    assert seen == ["looking at nodes", "dispatching sysadmin"]
    assert p.reply == "Found 2 nodes."
    assert p.resolved is True


def test_pretty_printed_multiline_objects():
    # A model that pretty-prints across newlines must NOT leak braces as text.
    seen: list[str] = []
    p = _StreamParser(seen.append)
    p.feed('{\n  "thinking": "step one"\n}\n')
    p.feed('{\n  "reply": "all done",\n  "resolved": false\n}')
    p.close()
    assert seen == ["step one"]
    assert p.reply == "all done"
    assert p.resolved is False


def test_plain_prose_fallback_when_model_ignores_contract():
    # No JSON at all → the whole text becomes the reply (never dropped), and no
    # spurious thinking steps.
    seen: list[str] = []
    p = _StreamParser(seen.append)
    _run(p, "just a plain answer\nwith two lines")
    assert seen == []
    assert p.reply == "just a plain answer\nwith two lines"
    assert p.resolved is False


def test_tolerates_markdown_code_fences():
    seen: list[str] = []
    p = _StreamParser(seen.append)
    _run(p, '```json\n{"thinking":"x"}\n{"reply":"y"}\n```')
    assert seen == ["x"]
    assert p.reply == "y"


def test_reply_only_no_thinking():
    seen: list[str] = []
    p = _StreamParser(seen.append)
    _run(p, '{"reply": "quick answer", "resolved": true}')
    assert seen == []
    assert p.reply == "quick answer"
    assert p.resolved is True


def test_blank_thinking_steps_are_ignored():
    seen: list[str] = []
    p = _StreamParser(seen.append)
    _run(p, '{"thinking": "   "}\n{"thinking": "real"}\n{"reply": "ok"}')
    assert seen == ["real"]
    assert p.reply == "ok"
