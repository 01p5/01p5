"""
Terminal — operator-driven SSH shell sessions hosted in the dashboard pod.

Design (TERM.1):
================

The dashboard pod owns a pool of long-lived ``TerminalSession``s. Each
session is a ``pty`` fork wrapping a single ``ssh`` subprocess
configured to dial a host from the user-managed inventory (INV.* work).
The session keeps:

- the raw pty file descriptor (read/write to/from the ssh client),
- the ssh ``subprocess.Popen`` handle (for liveness + reap),
- a bounded scrollback ring (most recent ~100KB of output bytes),
- ownership metadata (which authenticated user opened it),
- WebSocket attachment state (None when no browser is connected;
  ``last_detached_at`` updated when the WS drops so the expiry tick
  can SIGHUP stale sessions).

The WebSocket bridge + REST endpoints land in TERM.2; this module is
pure stdlib so the unit tests can drive it without a network surface.
A test can replace the ``ssh`` exec with a benign ``cat``-loop via the
``executable`` + ``args`` overrides on ``SessionManager.create``.

Why no asyncio: the rest of the dashboard is sync threading; mixing in
asyncio for one feature would be unkind to whoever has to debug it.
Each PTY session pumps from its own helper thread when attached.

Expiry:
  - **Attached** (a WS is currently bound to the session) → no expiry.
    The user can leave a tab open all day with zero keystrokes.
  - **Detached** (WS dropped or never connected) → SIGHUP after
    DETACHED_EXPIRY_SECONDS (default 1800 = 30 min).
  - **Dead** (ssh exited) → eligible for cleanup on the next tick.

Per-user cap: none. Trust the user; the chart's resource limits cap
the total pod budget at the kubernetes layer.

Security note: this is real ssh. The PTY runs as the dashboard pod's
user with whatever the inventory key authorizes on the target host.
We do not gate keystrokes through the approval queue — it's the
operator's shell. The audit log still captures the session create/
close events with the host_alias + ssh_user so there's a paper trail.
"""
from __future__ import annotations

import dataclasses
import fcntl
import logging
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Optional


logger = logging.getLogger(__name__)


DETACHED_EXPIRY_SECONDS = 1800   # 30 min — see module docstring
SCROLLBACK_BYTES = 100 * 1024    # ~100KB ring per session
_SSH_DEFAULT_ARGS = (
    # accept-new = TOFU; matches the ansible agent's convention. Real
    # operators on a public-facing demo can't be expected to manage
    # known_hosts entries by hand.
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ServerAliveInterval=30",
    "-o", "BatchMode=no",     # interactive: allow password prompts if key auth fails
    # Force remote pty allocation. Without ``-tt`` (double -t) ssh
    # won't allocate a tty when its own stdin isn't *itself* a tty —
    # which can happen depending on how the parent's pty is wired —
    # and TUIs like htop / top / vim / less fail with "Error opening
    # terminal: unknown.". The forced pty ensures the remote shell
    # has full job control + signal handling.
    "-tt",
)


# Environment overrides we apply on top of the dashboard pod's env when
# launching ssh. ``TERM`` is the load-bearing one: ssh forwards it to
# the remote shell on pty allocation, and the remote shell sets it as
# its own TERM. Default the pod's env almost never has it, and ssh
# falls back to "dumb" which breaks every curses-based TUI.
_SSH_ENV_OVERRIDES = {
    "TERM": "xterm-256color",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}

# TERM.9a — registry of local CLI session kinds. ``create(kind=...)``
# looks up the argv here instead of building an ssh command. Keeping
# this as a closed allowlist (rather than a free-form executable) is
# defense-in-depth: even a buggy / malicious POST to /terminal/sessions
# can't launch arbitrary binaries. Add new kinds by registering them
# here + (optionally) surfacing a button in the frontend new-session
# modal.
_LOCAL_KINDS: dict[str, tuple[str, ...]] = {
    "olympus-tui": ("olympus-tui", "--router", "manual"),
}


@dataclasses.dataclass
class TerminalSession:
    """One live PTY-wrapped subprocess.

    Two flavours — distinguished by ``kind``:
      - ``kind=None`` (default): ssh session to a remote inventory host.
        ``host_alias`` / ``ssh_user`` / ``address`` describe the target.
      - ``kind="olympus-tui"`` (or another entry in ``_LOCAL_KINDS``):
        local CLI hosted inside the dashboard pod. ``host_alias``
        is just a display label; ``address`` is ``(local)``.

    Owned by ``SessionManager``; callers must not mutate ``_master_fd``
    or ``_proc`` directly. ``write_input`` / ``read_scrollback`` /
    ``attach`` / ``detach`` are the supported API.
    """

    session_id: str
    owner_email: str
    host_alias: str
    ssh_user: str
    address: str
    created_at: float
    # Internal pty + process handles.
    _master_fd: int
    _proc: subprocess.Popen[bytes]
    # On-disk key file the ssh subprocess was launched with; we own
    # cleanup when the session closes.
    _key_path: Optional[str]
    # Scrollback ring. Bounded byte buffer; replay on (re)connect.
    _scrollback: deque[bytes] = dataclasses.field(default_factory=deque)
    _scrollback_bytes: int = 0
    # Attachment + expiry bookkeeping.
    attached: bool = False
    last_attached_at: Optional[float] = None
    last_detached_at: Optional[float] = None
    last_active_at: float = dataclasses.field(default_factory=time.time)
    closed: bool = False
    # TERM.9a — None for ssh sessions, one of ``_LOCAL_KINDS`` keys for
    # local CLI sessions. Surfaced on the wire so the UI can label them
    # differently in the rail / list.
    kind: Optional[str] = None

    # ---- public API ----

    def is_alive(self) -> bool:
        """True if the underlying ssh subprocess is still running."""
        return not self.closed and self._proc.poll() is None

    def write_input(self, data: bytes) -> None:
        """Push browser-side keystrokes into the pty.

        Raises ``BrokenPipeError`` if the session is dead; callers
        should react by closing the WS.
        """
        if self.closed:
            raise BrokenPipeError(f"session {self.session_id} is closed")
        os.write(self._master_fd, data)
        self.last_active_at = time.time()

    def read_scrollback(self, last_n_bytes: Optional[int] = None) -> bytes:
        """Return the most recent scrollback bytes.

        ``last_n_bytes=None`` returns the full ring (≤ ``SCROLLBACK_BYTES``).
        Otherwise returns at most that many bytes from the tail.
        """
        full = b"".join(self._scrollback)
        if last_n_bytes is None or last_n_bytes >= len(full):
            return full
        return full[-last_n_bytes:]

    def attach(self) -> None:
        self.attached = True
        self.last_attached_at = time.time()
        self.last_detached_at = None

    def detach(self) -> None:
        self.attached = False
        self.last_detached_at = time.time()

    # ---- internal helpers — used by SessionManager only ----

    def _record_output(self, data: bytes) -> None:
        """Append output bytes to the scrollback ring, dropping the
        oldest chunks until the total stays under ``SCROLLBACK_BYTES``.
        Each chunk is whatever ``os.read`` handed us — we don't try to
        align on lines, which is correct for raw pty bytes.

        Edge case: a single chunk larger than the ring (huge ``cat``
        of a binary, e.g.) gets pre-truncated to its tail bytes so we
        never end up with an empty ring after eviction."""
        if not data:
            return
        if len(data) > SCROLLBACK_BYTES:
            data = data[-SCROLLBACK_BYTES:]
        self._scrollback.append(data)
        self._scrollback_bytes += len(data)
        # Keep at least the most recent chunk no matter what.
        while self._scrollback_bytes > SCROLLBACK_BYTES and len(self._scrollback) > 1:
            evicted = self._scrollback.popleft()
            self._scrollback_bytes -= len(evicted)

    def _close(self, signal_first: bool = True) -> None:
        """Tear down: SIGHUP the ssh subprocess, close the master fd,
        unlink the key file. Idempotent."""
        if self.closed:
            return
        self.closed = True
        if signal_first and self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGHUP)
            except (ProcessLookupError, OSError):
                pass
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        if self._key_path:
            try:
                os.unlink(self._key_path)
            except OSError:
                pass


@dataclasses.dataclass
class SessionInfo:
    """Wire-friendly view of a session for list/create REST responses.

    Mirrors what TERM.2's ``GET /terminal/sessions`` will serialize —
    keeping it here so TERM.1's tests pin the shape early."""
    session_id: str
    host_alias: str
    ssh_user: str
    address: str
    created_at: float
    attached: bool
    last_active_at: float
    alive: bool
    # TERM.9a — None for ssh sessions, else the local-CLI kind label
    # (e.g. "olympus-tui"). Frontend uses it to pick the right
    # row icon + skip the host-picker for local sessions.
    kind: Optional[str] = None


class SessionManager:
    """Owns the pool of live PTY sessions; thread-safe.

    Public surface: ``create`` / ``get`` / ``list_for_user`` /
    ``close`` / ``tick_expiry`` / ``shutdown_all``. The WebSocket
    bridge (TERM.2) drives attach/detach + byte forwarding through the
    session object directly.
    """

    def __init__(
        self,
        *,
        detached_expiry_seconds: float = DETACHED_EXPIRY_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.RLock()
        self._detached_expiry = detached_expiry_seconds
        self._clock = clock

    def create(
        self,
        *,
        owner_email: str,
        host_alias: str,
        ssh_user: str,
        address: str,
        key_content: Optional[str] = None,
        ssh_port: int = 22,
        kind: Optional[str] = None,
        executable: Optional[str] = None,
        args: Optional[list[str]] = None,
        cwd: Optional[str] = None,
    ) -> TerminalSession:
        """Spawn a new pty-wrapped subprocess.

        Two modes:
          - **SSH (default, ``kind=None``):** builds an ssh command
            against ``ssh_user@address`` with ``key_content`` (if any)
            materialized to a 0600 tempfile. Cleanup on ``close``.
          - **Local CLI (``kind`` set):** looks up ``kind`` in
            ``_LOCAL_KINDS`` and spawns that argv directly. No ssh,
            no key. ``host_alias`` becomes the display label.

        ``executable`` + ``args`` is a test seam — overrides both
        modes. Tests pass ``cat`` or similar to exercise the pty
        plumbing without a real ssh daemon or TUI.
        """
        key_path: Optional[str] = None
        if key_content and kind is None:
            fd, key_path = tempfile.mkstemp(prefix="olympus-term-", suffix=".pem")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(key_content)
                    if not key_content.endswith("\n"):
                        fh.write("\n")
                os.chmod(key_path, 0o600)
            except OSError:
                if key_path:
                    try:
                        os.unlink(key_path)
                    except OSError:
                        pass
                raise

        if executable is None:
            if kind is not None:
                local = _LOCAL_KINDS.get(kind)
                if local is None:
                    raise ValueError(
                        f"unknown terminal session kind {kind!r} "
                        f"(known: {sorted(_LOCAL_KINDS)})"
                    )
                # ``shutil.which`` resolves the executable on PATH so
                # we fail fast with a clear log if (e.g.) olympus-tui
                # wasn't installed in the image.
                resolved = shutil.which(local[0])
                if resolved is None:
                    raise FileNotFoundError(
                        f"{local[0]!r} not on PATH — local kind {kind!r} can't launch"
                    )
                cmd = [resolved, *local[1:]]
            else:
                executable = shutil.which("ssh") or "ssh"
                cmd = [executable, *_SSH_DEFAULT_ARGS]
                if key_path:
                    cmd += ["-i", key_path]
                if ssh_port and ssh_port != 22:
                    cmd += ["-p", str(ssh_port)]
                cmd += [f"{ssh_user}@{address}"]
        else:
            cmd = [executable, *(args or [])]

        master_fd, slave_fd = pty.openpty()
        # Inherit the dashboard pod's env, then layer TERM / LANG / LC_ALL
        # so the remote shell ssh allocates gets a sensible terminal
        # type (xterm-256color) — without this, htop/top/vim/less all
        # fail with "Error opening terminal: unknown.".
        env = os.environ.copy()
        env.update(_SSH_ENV_OVERRIDES)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                close_fds=True,
                preexec_fn=os.setsid,  # detach from dashboard's pgroup
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            if key_path:
                try:
                    os.unlink(key_path)
                except OSError:
                    pass
            raise
        # Parent doesn't need the slave; the subprocess owns it now.
        os.close(slave_fd)

        session = TerminalSession(
            session_id=uuid.uuid4().hex,
            owner_email=owner_email,
            host_alias=host_alias,
            ssh_user=ssh_user,
            address=address,
            created_at=self._clock(),
            _master_fd=master_fd,
            _proc=proc,
            _key_path=key_path,
            last_detached_at=self._clock(),  # starts detached until WS attaches
            last_active_at=self._clock(),
            kind=kind,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Optional[TerminalSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_for_user(self, owner_email: str) -> list[TerminalSession]:
        """Return only sessions owned by this user — no cross-user
        visibility even at the list level."""
        with self._lock:
            return [
                s for s in self._sessions.values()
                if s.owner_email == owner_email
            ]

    def close(self, session_id: str) -> bool:
        """Close + remove a session. Returns True if it existed."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session._close()  # noqa: SLF001 — manager-internal lifecycle
        return True

    def pump_once(self, session: TerminalSession, timeout: float = 0.05) -> bytes:
        """Drain whatever pty output is currently buffered.

        Returns the bytes read (possibly empty) and appends them to the
        scrollback. The WebSocket bridge calls this in its tight read
        loop; tests call it to assert what the pty emitted.
        """
        if session.closed:
            return b""
        try:
            ready, _, _ = select.select([session._master_fd], [], [], timeout)  # noqa: SLF001
        except (OSError, ValueError):
            return b""
        if not ready:
            return b""
        try:
            data = os.read(session._master_fd, 4096)  # noqa: SLF001
        except OSError:
            return b""
        if data:
            session._record_output(data)  # noqa: SLF001
            session.last_active_at = self._clock()
        return data

    def tick_expiry(self) -> list[str]:
        """Sweep: close any detached session whose grace period expired,
        plus any whose underlying ssh subprocess has died. Returns the
        list of session_ids that were closed.

        The dashboard's main loop calls this periodically (or a tiny
        background thread does). Idempotent — safe to call as often as
        you like.
        """
        now = self._clock()
        closed_ids: list[str] = []
        with self._lock:
            for sid, session in list(self._sessions.items()):
                if not session.is_alive():
                    self._sessions.pop(sid, None)
                    session._close(signal_first=False)  # noqa: SLF001
                    closed_ids.append(sid)
                    continue
                if (
                    not session.attached
                    and session.last_detached_at is not None
                    and now - session.last_detached_at >= self._detached_expiry
                ):
                    self._sessions.pop(sid, None)
                    session._close()  # noqa: SLF001
                    closed_ids.append(sid)
        return closed_ids

    def shutdown_all(self) -> None:
        """Tear down every session — called on dashboard shutdown."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s._close()  # noqa: SLF001

    def resize(
        self,
        session: TerminalSession,
        *,
        cols: int,
        rows: int,
    ) -> None:
        """Push a new window size to the session's pty.

        TIOCSWINSZ on the master fd updates the kernel's record + the
        kernel auto-sends SIGWINCH to the slave's foreground process
        group, so curses-based TUIs (htop, vim, less) redraw on the
        next event loop tick without any extra signaling from us.

        Silently bails on a closed session or a bogus size; this is
        called from the browser so we don't trust the values blindly
        (clamp at 1..1000 cols, 1..500 rows). Any ioctl error is
        logged at debug and swallowed — a failed resize must NOT take
        down the session.
        """
        if session.closed:
            return
        try:
            cols_clean = max(1, min(int(cols), 1000))
            rows_clean = max(1, min(int(rows), 500))
        except (TypeError, ValueError):
            return
        # struct winsize { ws_row; ws_col; ws_xpixel; ws_ypixel; }
        packed = struct.pack("HHHH", rows_clean, cols_clean, 0, 0)
        try:
            fcntl.ioctl(session._master_fd, termios.TIOCSWINSZ, packed)  # noqa: SLF001
        except OSError as exc:
            logger.debug("TIOCSWINSZ on session %s failed: %s",
                         session.session_id, exc)


def session_to_info(session: TerminalSession) -> SessionInfo:
    """Project a TerminalSession into the wire-friendly SessionInfo
    shape. Pulled out so TERM.2's REST serializer can reuse it +
    TERM.1's tests can lock it in."""
    return SessionInfo(
        session_id=session.session_id,
        host_alias=session.host_alias,
        ssh_user=session.ssh_user,
        address=session.address,
        created_at=session.created_at,
        attached=session.attached,
        last_active_at=session.last_active_at,
        alive=session.is_alive(),
        kind=session.kind,
    )


__all__ = [
    "DETACHED_EXPIRY_SECONDS",
    "SCROLLBACK_BYTES",
    "SessionInfo",
    "SessionManager",
    "TerminalSession",
    "session_to_info",
]


def _ignored(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
    """Anchor for pytype / mypy; intentionally unused."""
    del args, kwargs
