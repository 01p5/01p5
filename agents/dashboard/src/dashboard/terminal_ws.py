"""
TERM.2b — WebSocket bridge between the browser xterm.js client and a
``TerminalSession``'s PTY.

The dashboard's request handler is threading-based ``BaseHTTPRequestHandler``,
not asyncio. We hand-roll the HTTP/1.1 → WS upgrade (10 lines of
``Sec-WebSocket-Accept`` math) and then drive ``wsproto`` (sans-IO) for
the frame phase. Each connected client gets its own thread:

- main thread (this handler): reads incoming WS frames, writes browser
  keystrokes into the pty.
- helper thread (spawned on connect): pumps pty bytes out → WS BINARY
  frames to the browser.

On WS close OR pty EOF, both sides tear down and the session detaches
(SessionManager's expiry tick will SIGHUP it after the grace period if
no one reattaches).

Frame protocol:
- Browser → server: TEXT or BINARY (we accept either). Payload is raw
  bytes to write into the pty.
- Server → browser: BINARY. Payload is raw pty output bytes (terminal
  expects ANSI/bytes, not utf-8 strings).
- PING / PONG / CLOSE: standard WS control-frame handling.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import select
import socket
import threading
import time
from typing import Optional

try:
    from wsproto import WSConnection
    from wsproto.connection import ConnectionType
    from wsproto.events import (
        AcceptConnection,
        BytesMessage,
        CloseConnection,
        Ping,
        Pong,
        Request,
        TextMessage,
    )
    from wsproto.frame_protocol import CloseReason
    _WSPROTO_AVAILABLE = True
except ImportError:  # pragma: no cover — runtime require
    _WSPROTO_AVAILABLE = False


from .terminal import SessionManager, TerminalSession


logger = logging.getLogger(__name__)


_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_PUMP_READ_TIMEOUT_S = 0.05


def _accept_key(client_key: str) -> str:
    """Compute the Sec-WebSocket-Accept value for the handshake reply."""
    digest = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


class WSHandshakeError(Exception):
    """Raised when the client's request isn't a valid WS upgrade."""


def perform_ws_handshake(
    *,
    requestline: str,
    headers,
    write_response,
) -> "WSConnection":
    """Drive the upgrade through wsproto's server-side state machine.

    BaseHTTPRequestHandler already parsed the request line + headers
    out of the wire bytes; we reconstruct them so wsproto can validate
    the upgrade AND end up in the OPEN state ready to emit/parse
    frames. Returns the WSConnection — the caller drives it for the
    rest of the session.

    ``headers`` is the handler's ``self.headers`` (a
    ``http.client.HTTPMessage``). ``write_response`` is a callable
    that writes bytes to the underlying socket.

    Raises ``WSHandshakeError`` on any handshake violation; caller
    should respond 400 instead of upgrading.
    """
    if not _WSPROTO_AVAILABLE:
        raise RuntimeError("wsproto is required for the terminal bridge")

    # Cheap pre-check so we can return a helpful error before wsproto
    # parses (its error messages are protocol-level + unfriendly).
    upgrade = (headers.get("Upgrade") or "").strip().lower()
    if upgrade != "websocket":
        raise WSHandshakeError(f"Upgrade header must be 'websocket' (got {upgrade!r})")
    connection = (headers.get("Connection") or "").strip().lower()
    if "upgrade" not in connection:
        raise WSHandshakeError(
            f"Connection header must include 'upgrade' (got {connection!r})"
        )
    if not (headers.get("Sec-WebSocket-Key") or "").strip():
        raise WSHandshakeError("missing Sec-WebSocket-Key")
    version = (headers.get("Sec-WebSocket-Version") or "").strip()
    if version != "13":
        raise WSHandshakeError(f"unsupported Sec-WebSocket-Version {version!r} (need 13)")

    # Reconstruct the raw HTTP request and feed wsproto so its server
    # state machine advances Handshake → Connected naturally.
    raw = f"{requestline}\r\n{headers.as_string()}".encode("iso-8859-1")
    ws = WSConnection(ConnectionType.SERVER)
    ws.receive_data(raw)

    request_event = None
    for event in ws.events():
        if isinstance(event, Request):
            request_event = event
            break
    if request_event is None:
        raise WSHandshakeError("wsproto could not parse upgrade request")

    # Tell wsproto we're accepting; .send() returns the 101 + headers
    # to write to the socket.
    write_response(ws.send(AcceptConnection()))
    return ws


def bridge_session_to_socket(
    *,
    session: TerminalSession,
    manager: SessionManager,
    sock: socket.socket,
    ws: "WSConnection",
    stop_after_seconds: Optional[float] = None,
) -> None:
    """Drive the WS-frame phase of a connected browser client against
    the given session's pty. Blocks until either side closes.

    ``ws`` must already be in OPEN state — obtain via
    ``perform_ws_handshake``. The bridge keeps a write lock on the
    socket so the pump thread + main thread don't interleave frames.

    ``stop_after_seconds`` is a test seam — tests pass a small ceiling so
    the helper thread doesn't hang the suite if the client misbehaves.
    """
    if not _WSPROTO_AVAILABLE:
        raise RuntimeError("wsproto is required for the terminal bridge")

    write_lock = threading.Lock()  # one writer at a time on the WS

    def _send(payload: bytes) -> bool:
        with write_lock:
            try:
                sock.sendall(payload)
                return True
            except (OSError, BrokenPipeError):
                return False

    # Scrollback replay: send the buffered history first so a reconnect
    # picks up where the user left off.
    history = session.read_scrollback()
    if history:
        if not _send(ws.send(BytesMessage(data=history))):
            return

    session.attach()

    stop_event = threading.Event()

    def _pump_pty_to_ws() -> None:
        """Push pty output to the browser. Runs until stop_event set."""
        deadline = (
            None if stop_after_seconds is None
            else time.monotonic() + stop_after_seconds
        )
        while not stop_event.is_set():
            if deadline is not None and time.monotonic() > deadline:
                return
            chunk = manager.pump_once(session, timeout=_PUMP_READ_TIMEOUT_S)
            if chunk:
                if not _send(ws.send(BytesMessage(data=chunk))):
                    return
            elif not session.is_alive():
                # Subprocess exited — graceful close.
                try:
                    _send(ws.send(CloseConnection(code=CloseReason.NORMAL_CLOSURE)))
                except Exception:
                    pass
                return

    pump_thread = threading.Thread(
        target=_pump_pty_to_ws, name=f"term-pump-{session.session_id[:8]}",
        daemon=True,
    )
    pump_thread.start()

    try:
        # Browser → pty loop. select on sock so we can wake periodically
        # and notice a stop_event flip.
        sock_deadline = (
            None if stop_after_seconds is None
            else time.monotonic() + stop_after_seconds
        )
        while not stop_event.is_set():
            if sock_deadline is not None and time.monotonic() > sock_deadline:
                break
            try:
                ready, _, _ = select.select([sock], [], [], 0.1)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                data = sock.recv(4096)
            except (OSError, ConnectionError):
                break
            if not data:
                # Client closed TCP without a WS close frame.
                break
            ws.receive_data(data)
            for event in ws.events():
                if isinstance(event, (BytesMessage, TextMessage)):
                    # Browser keystrokes / pasted text → pty stdin.
                    payload = event.data if isinstance(event.data, bytes) \
                        else event.data.encode("utf-8")
                    try:
                        session.write_input(payload)
                    except BrokenPipeError:
                        _send(ws.send(CloseConnection(code=CloseReason.NORMAL_CLOSURE)))
                        stop_event.set()
                        break
                elif isinstance(event, Ping):
                    _send(ws.send(Pong(payload=event.payload)))
                elif isinstance(event, CloseConnection):
                    # Echo close back per RFC, then exit.
                    _send(ws.send(event.response()))
                    stop_event.set()
                    break
    finally:
        stop_event.set()
        pump_thread.join(timeout=1.0)
        session.detach()


__all__ = [
    "WSHandshakeError",
    "bridge_session_to_socket",
    "perform_ws_handshake",
]
