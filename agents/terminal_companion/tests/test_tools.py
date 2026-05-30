"""
TERM.4a — read_terminal_scrollback tool + strip_ansi helper.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from terminal_companion.tools import make_read_scrollback_tool, strip_ansi


# ---------------------------------------------------------------------------
# strip_ansi
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_csi_sequences():
    # \x1b[31m sets red; \x1b[0m resets.
    raw = b"hello \x1b[31mworld\x1b[0m!"
    assert strip_ansi(raw) == b"hello world!"


def test_strip_ansi_removes_osc_sequences():
    # \x1b]0; set window title to "title"\x07
    raw = b"\x1b]0;my title\x07prompt$"
    assert strip_ansi(raw) == b"prompt$"


def test_strip_ansi_removes_lone_escape_sequences():
    # ESC c = full terminal reset.
    raw = b"before\x1bcafter"
    assert strip_ansi(raw) == b"beforeafter"


def test_strip_ansi_preserves_plain_text():
    assert strip_ansi(b"just plain bytes\n") == b"just plain bytes\n"


def test_strip_ansi_handles_complex_prompt():
    """A typical bash prompt with color escapes + window title."""
    raw = (
        b"\x1b]0;user@host: /tmp\x07"
        b"\x1b[01;32muser@host\x1b[00m:\x1b[01;34m/tmp\x1b[00m$ "
    )
    assert strip_ansi(raw) == b"user@host:/tmp$ "


# ---------------------------------------------------------------------------
# make_read_scrollback_tool
# ---------------------------------------------------------------------------


def _stub_session(*, owner: str, scrollback: bytes):
    s = MagicMock()
    s.owner_email = owner
    s.read_scrollback = MagicMock(return_value=scrollback)
    return s


def _stub_manager(sessions: dict):
    m = MagicMock()
    m.get = MagicMock(side_effect=lambda sid: sessions.get(sid))
    return m


def test_tool_reads_default_session_when_no_id_passed():
    sess = _stub_session(owner="alice@x", scrollback=b"hello world")
    mgr = _stub_manager({"sess-1": sess})
    tool = make_read_scrollback_tool(
        session_manager=mgr, owner_email="alice@x", default_session_id="sess-1",
    )
    out = tool.invoke({})
    assert "hello world" in out
    sess.read_scrollback.assert_called_once()


def test_tool_strips_ansi_before_returning():
    sess = _stub_session(
        owner="alice@x",
        scrollback=b"\x1b[31mERR\x1b[0m: file not found",
    )
    mgr = _stub_manager({"sess-1": sess})
    tool = make_read_scrollback_tool(
        session_manager=mgr, owner_email="alice@x", default_session_id="sess-1",
    )
    out = tool.invoke({})
    assert out == "ERR: file not found"


def test_tool_accepts_explicit_session_id_arg():
    sess_a = _stub_session(owner="alice@x", scrollback=b"AAA")
    sess_b = _stub_session(owner="alice@x", scrollback=b"BBB")
    mgr = _stub_manager({"sess-a": sess_a, "sess-b": sess_b})
    tool = make_read_scrollback_tool(
        session_manager=mgr, owner_email="alice@x", default_session_id="sess-a",
    )
    out = tool.invoke({"session_id": "sess-b"})
    assert "BBB" in out
    sess_a.read_scrollback.assert_not_called()


def test_tool_passes_last_n_bytes_to_session_with_cap():
    sess = _stub_session(owner="alice@x", scrollback=b"x")
    mgr = _stub_manager({"sess-1": sess})
    tool = make_read_scrollback_tool(
        session_manager=mgr, owner_email="alice@x", default_session_id="sess-1",
    )
    # Within the 64KB cap → passes through.
    tool.invoke({"last_n_bytes": 1024})
    assert sess.read_scrollback.call_args.kwargs["last_n_bytes"] == 1024

    sess.read_scrollback.reset_mock()
    # Above the cap → clamps to 64KB.
    tool.invoke({"last_n_bytes": 999_999})
    assert sess.read_scrollback.call_args.kwargs["last_n_bytes"] == 64 * 1024

    sess.read_scrollback.reset_mock()
    # Below 1 → floored to 1.
    tool.invoke({"last_n_bytes": 0})
    assert sess.read_scrollback.call_args.kwargs["last_n_bytes"] == 1


def test_tool_returns_error_on_unknown_session():
    mgr = _stub_manager({})
    tool = make_read_scrollback_tool(
        session_manager=mgr, owner_email="alice@x", default_session_id="sess-missing",
    )
    out = tool.invoke({})
    assert out.startswith("ERROR")
    assert "not found" in out


def test_tool_treats_foreign_owned_session_as_not_found():
    """Don't leak existence — same posture as the REST endpoint."""
    sess = _stub_session(owner="bob@x", scrollback=b"secret")
    mgr = _stub_manager({"sess-1": sess})
    tool = make_read_scrollback_tool(
        session_manager=mgr, owner_email="alice@x", default_session_id="sess-1",
    )
    out = tool.invoke({})
    assert "not found" in out
    # And no scrollback bytes leaked into the response.
    assert "secret" not in out


def test_tool_returns_error_with_no_session_id_and_no_default():
    mgr = _stub_manager({})
    tool = make_read_scrollback_tool(
        session_manager=mgr, owner_email="alice@x", default_session_id="",
    )
    out = tool.invoke({"session_id": ""})
    assert "no session_id" in out
