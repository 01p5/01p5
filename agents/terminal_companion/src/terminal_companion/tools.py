"""
Tools for the terminal_companion agent.

Single tool today: ``read_terminal_scrollback`` — pulls the most recent
N bytes of pty output for a session. Strips ANSI escape sequences before
handing to the LLM so the model sees the text the user sees on screen,
not raw escape codes that bloat the prompt without adding signal.

Context-bound: the tool is built per-ask via a factory that closes over
the SessionManager + the operator's authenticated email. The session
lookup re-checks ownership inside the closure so a prompt-injected
session_id can't pull another user's scrollback.
"""
from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import tool


# ANSI escape sequences:
#  - CSI: \x1b[ ... letter
#  - OSC: \x1b] ... BEL or ST
#  - Lone two-byte ESC sequences: \x1b followed by any "Fe" (0x40-0x5F)
#    OR "Fs" (0x60-0x7E) byte — e.g. \x1bc (reset, RIS), \x1bD (index).
# Stripping these gives the LLM what the user sees on screen rather
# than the raw byte stream.
_ANSI_CSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_OTHER = re.compile(rb"\x1b[@-~]")


def strip_ansi(data: bytes) -> bytes:
    """Remove ANSI escape sequences from raw pty bytes.

    Exported so tests can pin the regexes against known sequences."""
    data = _ANSI_CSI.sub(b"", data)
    data = _ANSI_OSC.sub(b"", data)
    data = _ANSI_OTHER.sub(b"", data)
    return data


def make_read_scrollback_tool(
    *,
    session_manager: Any,
    owner_email: str,
    default_session_id: str,
) -> Any:
    """Return a ``read_terminal_scrollback`` @tool closure that reads
    only the calling user's sessions.

    ``default_session_id`` is the session the user is currently looking
    at — the LLM may call the tool with a different id (e.g. to compare
    two terminals), but if it omits the arg we default to the one the
    user is actively asking about.
    """

    @tool
    def read_terminal_scrollback(
        session_id: str = "",
        last_n_bytes: int = 8192,
    ) -> str:
        """Read recent terminal output from one of the operator's live
        sessions. Returns ANSI-stripped text (what the user sees on
        screen). Default ``session_id`` is the currently-open session.

        Use this to answer questions like "what's happening in this
        shell", "why did the last command fail", "what's the current
        working directory". The output is read-only — calling this
        tool does NOT execute anything.
        """
        sid = (session_id or default_session_id or "").strip()
        if not sid:
            return "ERROR: no session_id provided and no current session bound"

        session = session_manager.get(sid)
        if session is None:
            return f"ERROR: session {sid!r} not found (may have expired or closed)"
        if session.owner_email != owner_email:
            # Don't leak existence — same posture as the REST DELETE.
            return f"ERROR: session {sid!r} not found"

        try:
            n = max(1, min(int(last_n_bytes), 64 * 1024))  # cap at 64KB
        except (TypeError, ValueError):
            n = 8192

        raw = session.read_scrollback(last_n_bytes=n)
        clean = strip_ansi(raw)
        try:
            return clean.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover — decode shouldn't fail
            return f"ERROR: scrollback decode failed: {type(exc).__name__}: {exc}"

    return read_terminal_scrollback


__all__ = ["make_read_scrollback_tool", "strip_ansi"]
