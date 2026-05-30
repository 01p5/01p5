"""
TERM.1 — SessionManager + TerminalSession + PTY launcher.

We don't shell out to a real ssh in these tests. The ``executable``
+ ``args`` override on ``SessionManager.create`` lets us spawn benign
processes (cat, echo, sh -c '...') so the pty plumbing, scrollback
ring, attach/detach, and expiry tick all exercise without a real
ssh daemon.
"""
from __future__ import annotations

import os
import time
from typing import Any

import pytest
from dashboard.terminal import (
    DETACHED_EXPIRY_SECONDS,
    SCROLLBACK_BYTES,
    SessionManager,
    session_to_info,
)


def _drain(mgr: SessionManager, session: Any, timeout: float = 2.0) -> bytes:
    """Read everything the pty has emitted, up to a deadline."""
    out = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = mgr.pump_once(session, timeout=0.05)
        if chunk:
            out.extend(chunk)
        elif out:
            # Got data and then a quiet tick — assume the burst is done.
            break
    return bytes(out)


@pytest.fixture
def mgr():
    m = SessionManager(detached_expiry_seconds=0.1)
    yield m
    m.shutdown_all()


# ---------------------------------------------------------------------------
# create + lifecycle
# ---------------------------------------------------------------------------


def test_create_returns_session_with_metadata(mgr):
    session = mgr.create(
        owner_email="alice@stanford.edu",
        host_alias="cp",
        ssh_user="ubuntu",
        address="10.0.0.1",
        executable="/bin/cat",
        args=[],
    )
    assert session.session_id
    assert session.owner_email == "alice@stanford.edu"
    assert session.host_alias == "cp"
    assert session.ssh_user == "ubuntu"
    assert session.address == "10.0.0.1"
    assert session.is_alive() is True
    assert session.attached is False
    # Detached timer starts right away so a session that nobody ever
    # connects to still expires after the grace period.
    assert session.last_detached_at is not None


def test_get_returns_session_or_none(mgr):
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    assert mgr.get(s.session_id) is s
    assert mgr.get("does-not-exist") is None


def test_list_for_user_isolates_by_owner(mgr):
    a = mgr.create(owner_email="alice@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    b = mgr.create(owner_email="bob@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    assert {s.session_id for s in mgr.list_for_user("alice@x")} == {a.session_id}
    assert {s.session_id for s in mgr.list_for_user("bob@x")} == {b.session_id}
    assert mgr.list_for_user("nobody@x") == []


def test_close_removes_session_and_returns_true(mgr):
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    sid = s.session_id
    assert mgr.close(sid) is True
    assert mgr.get(sid) is None
    assert mgr.close(sid) is False  # second close is a no-op


def test_session_closed_after_close(mgr):
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    mgr.close(s.session_id)
    # is_alive flips false; write raises BrokenPipeError.
    assert s.is_alive() is False
    with pytest.raises(BrokenPipeError):
        s.write_input(b"hi")


# ---------------------------------------------------------------------------
# pty bytes round-trip + scrollback
# ---------------------------------------------------------------------------


def test_write_input_echoes_back_through_pty(mgr):
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    s.write_input(b"hello\n")
    out = _drain(mgr, s)
    # Pty in default canonical+echo mode echoes input back.
    assert b"hello" in out


def test_scrollback_accumulates_output(mgr):
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    s.write_input(b"first line\n")
    _drain(mgr, s)
    s.write_input(b"second line\n")
    _drain(mgr, s)
    sb = s.read_scrollback()
    assert b"first line" in sb
    assert b"second line" in sb


def test_scrollback_caps_at_size_limit():
    # Use a fresh manager so we can verify the eviction math without
    # interference from other sessions.
    mgr = SessionManager()
    try:
        s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                       address="a", executable="/bin/cat")
        # Synthesize a huge output: cat doesn't help, so we feed the
        # scrollback recorder directly via the internal API.
        from dashboard.terminal import SCROLLBACK_BYTES as LIMIT
        s._record_output(b"A" * (LIMIT + 500))  # noqa: SLF001
        assert s._scrollback_bytes <= LIMIT  # noqa: SLF001
        sb = s.read_scrollback()
        assert len(sb) <= LIMIT
        # Tail is still "A" — eviction drops oldest chunks first.
        assert sb.endswith(b"A")
    finally:
        mgr.shutdown_all()


def test_read_scrollback_tail_bytes(mgr):
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    s._record_output(b"alphabet-soup")  # noqa: SLF001
    assert s.read_scrollback(last_n_bytes=4) == b"soup"
    assert s.read_scrollback(last_n_bytes=None) == b"alphabet-soup"
    # Asking for more than we have returns the whole buffer.
    assert s.read_scrollback(last_n_bytes=999) == b"alphabet-soup"


# ---------------------------------------------------------------------------
# attach + detach + expiry
# ---------------------------------------------------------------------------


def test_attach_detach_toggles_flags(mgr):
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    s.attach()
    assert s.attached is True
    assert s.last_attached_at is not None
    assert s.last_detached_at is None
    s.detach()
    assert s.attached is False
    assert s.last_detached_at is not None


def test_tick_expiry_closes_detached_session_past_grace():
    mgr = SessionManager(detached_expiry_seconds=0.05)
    try:
        s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                       address="a", executable="/bin/cat")
        sid = s.session_id
        # Session is detached from creation — sleep past the grace, tick.
        time.sleep(0.10)
        closed = mgr.tick_expiry()
        assert sid in closed
        assert mgr.get(sid) is None
    finally:
        mgr.shutdown_all()


def test_tick_expiry_keeps_attached_session_alive_indefinitely():
    """Attached sessions never expire — the user is actively watching
    even if they're not typing. That's the whole 'detached vs idle'
    distinction."""
    mgr = SessionManager(detached_expiry_seconds=0.05)
    try:
        s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                       address="a", executable="/bin/cat")
        s.attach()
        time.sleep(0.10)
        assert mgr.tick_expiry() == []
        assert mgr.get(s.session_id) is s
    finally:
        mgr.shutdown_all()


def test_tick_expiry_reaps_dead_ssh_subprocess(mgr):
    # Spawn a process that exits immediately.
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/sh", args=["-c", "exit 0"])
    sid = s.session_id
    # Wait for the subprocess to actually die.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and s.is_alive():
        time.sleep(0.02)
    assert not s.is_alive()
    closed = mgr.tick_expiry()
    assert sid in closed
    assert mgr.get(sid) is None


def test_shutdown_all_closes_every_session():
    mgr = SessionManager()
    a = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    b = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    mgr.shutdown_all()
    assert mgr.list_for_user("u@x") == []
    assert not a.is_alive()
    assert not b.is_alive()


# ---------------------------------------------------------------------------
# key file lifecycle
# ---------------------------------------------------------------------------


_FAKE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "ZmFrZS1jb250ZW50LWZvci10ZXJtaW5hbC10ZXN0cw==\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)


def test_key_content_materializes_to_0600_tempfile(mgr):
    s = mgr.create(
        owner_email="u@x", host_alias="h", ssh_user="u",
        address="a", executable="/bin/cat",
        key_content=_FAKE_KEY,
    )
    assert s._key_path is not None  # noqa: SLF001
    kp = s._key_path  # noqa: SLF001
    assert os.path.exists(kp)
    assert os.stat(kp).st_mode & 0o777 == 0o600
    body = open(kp, "r", encoding="utf-8").read()
    assert "BEGIN OPENSSH PRIVATE KEY" in body


def test_key_file_unlinked_on_close(mgr):
    s = mgr.create(
        owner_email="u@x", host_alias="h", ssh_user="u",
        address="a", executable="/bin/cat",
        key_content=_FAKE_KEY,
    )
    kp = s._key_path  # noqa: SLF001
    assert kp and os.path.exists(kp)
    mgr.close(s.session_id)
    assert not os.path.exists(kp)


def test_no_key_path_when_key_content_omitted(mgr):
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat")
    assert s._key_path is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# session_to_info wire shape
# ---------------------------------------------------------------------------


def test_session_to_info_projects_wire_fields(mgr):
    s = mgr.create(owner_email="u@x", host_alias="cp", ssh_user="ubuntu",
                   address="10.0.0.1", executable="/bin/cat")
    info = session_to_info(s)
    assert info.session_id == s.session_id
    assert info.host_alias == "cp"
    assert info.ssh_user == "ubuntu"
    assert info.address == "10.0.0.1"
    assert info.attached is False
    assert info.alive is True
    # No raw fd / no Popen handle leaks into the wire shape.
    import dataclasses as _dc
    field_names = {f.name for f in _dc.fields(info)}
    assert "_master_fd" not in field_names
    assert "_proc" not in field_names
    assert "_scrollback" not in field_names


def test_module_constants_match_spec():
    """Quick smoke check that the module-level knobs match the design
    docs (30 min detached expiry, 100KB scrollback)."""
    assert DETACHED_EXPIRY_SECONDS == 1800
    assert SCROLLBACK_BYTES == 100 * 1024


def test_ssh_default_args_include_force_pty_and_keepalive():
    """``-tt`` forces remote pty allocation so TUIs (htop, top, vim,
    less) don't fail with 'Error opening terminal: unknown.'."""
    from dashboard.terminal import _SSH_DEFAULT_ARGS
    assert "-tt" in _SSH_DEFAULT_ARGS
    # Spot-check the other hardening flags are still present.
    assert "StrictHostKeyChecking=accept-new" in _SSH_DEFAULT_ARGS
    assert "ServerAliveInterval=30" in _SSH_DEFAULT_ARGS


def test_ssh_env_overrides_set_xterm_256color():
    """The remote shell's TERM is what ssh forwards from our local env.
    Pod's env almost never has TERM set, so we explicitly inject
    xterm-256color (+ utf-8 locale) before launching ssh."""
    from dashboard.terminal import _SSH_ENV_OVERRIDES
    assert _SSH_ENV_OVERRIDES["TERM"] == "xterm-256color"
    assert _SSH_ENV_OVERRIDES["LANG"] == "C.UTF-8"
    assert _SSH_ENV_OVERRIDES["LC_ALL"] == "C.UTF-8"


def test_create_passes_env_overrides_to_subprocess(mgr, monkeypatch):
    """End-to-end: create() must layer the TERM / LANG / LC_ALL
    overrides on top of the inherited env when spawning the
    subprocess. Catches a regression where someone drops the
    ``env.update(_SSH_ENV_OVERRIDES)`` call."""
    captured: dict = {}

    real_popen = __import__("subprocess").Popen

    def fake_popen(*args, **kwargs):
        captured["env"] = dict(kwargs.get("env", {}))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr("dashboard.terminal.subprocess.Popen", fake_popen)
    s = mgr.create(owner_email="u@x", host_alias="h", ssh_user="u",
                   address="a", executable="/bin/cat", args=[])
    assert captured["env"].get("TERM") == "xterm-256color"
    assert captured["env"].get("LANG") == "C.UTF-8"
    assert captured["env"].get("LC_ALL") == "C.UTF-8"
    # We didn't *replace* the env — pod env still flows through.
    assert "PATH" in captured["env"]
    mgr.close(s.session_id)
