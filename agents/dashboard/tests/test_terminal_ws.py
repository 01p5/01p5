"""
TERM.2b — WebSocket bridge.

Drive a real socket pair against ``bridge_session_to_socket`` so the
frame layer + scrollback replay + browser↔pty round-trip all exercise.
The handshake math gets a separate unit test (pure-function) to lock
in the Sec-WebSocket-Accept value against the RFC 6455 example.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest
from dashboard.terminal import SessionManager
from dashboard.terminal_ws import (
    WSHandshakeError,
    _accept_key,
    bridge_session_to_socket,
    perform_ws_handshake,
)
from wsproto import WSConnection
from wsproto.connection import ConnectionType
from wsproto.events import (
    AcceptConnection,
    BytesMessage,
    CloseConnection,
    Request,
)


class _MockHeaders:
    """Stands in for http.client.HTTPMessage — supports .get() and
    .as_string() (latter used to reconstruct the raw request for
    wsproto). The mock returns headers in the same blank-line-terminated
    shape the email parser produces."""
    def __init__(self, **fields):
        self._fields = fields  # preserve case in as_string

    def get(self, name, default=None):
        # Case-insensitive lookup.
        for k, v in self._fields.items():
            if k.lower() == name.lower():
                return v
        return default

    def as_string(self):
        return "".join(f"{k}: {v}\r\n" for k, v in self._fields.items()) + "\r\n"


# ---------------------------------------------------------------------------
# Handshake — pure functions
# ---------------------------------------------------------------------------


def test_accept_key_matches_rfc_6455_example():
    """RFC 6455 §1.3 — given client key 'dGhlIHNhbXBsZSBub25jZQ==' the
    expected Sec-WebSocket-Accept is 's3pPLMBiTxaQ9kYGzzhZRbK+xOo='.
    Pinning this catches any drift in the base64+sha1 dance."""
    assert _accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_perform_ws_handshake_writes_101_with_correct_accept():
    out = bytearray()
    ws = perform_ws_handshake(
        requestline="GET /terminal/sessions/abc/ws HTTP/1.1",
        headers=_MockHeaders(
            **{"Host": "localhost",
               "Upgrade": "websocket",
               "Connection": "Upgrade",
               "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
               "Sec-WebSocket-Version": "13"},
        ),
        write_response=out.extend,
    )
    text = bytes(out).decode("iso-8859-1")
    assert text.startswith("HTTP/1.1 101")
    # wsproto computes the same Sec-WebSocket-Accept value per RFC 6455.
    assert "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in text
    # Returned WSConnection is past handshake — frames can flow.
    assert ws is not None


@pytest.mark.parametrize("bad_headers,reason", [
    ({"Host": "h", "Upgrade": "h2c", "Connection": "Upgrade",
      "Sec-WebSocket-Key": "x", "Sec-WebSocket-Version": "13"}, "Upgrade header"),
    ({"Host": "h", "Upgrade": "websocket", "Connection": "keep-alive",
      "Sec-WebSocket-Key": "x", "Sec-WebSocket-Version": "13"}, "Connection header"),
    ({"Host": "h", "Upgrade": "websocket", "Connection": "Upgrade",
      "Sec-WebSocket-Version": "13"}, "Sec-WebSocket-Key"),
    ({"Host": "h", "Upgrade": "websocket", "Connection": "Upgrade",
      "Sec-WebSocket-Key": "x", "Sec-WebSocket-Version": "8"}, "Sec-WebSocket-Version"),
])
def test_perform_ws_handshake_rejects_bad_requests(bad_headers, reason):
    with pytest.raises(WSHandshakeError, match=reason):
        perform_ws_handshake(
            requestline="GET /ws HTTP/1.1",
            headers=_MockHeaders(**bad_headers),
            write_response=lambda _b: None,
        )


# ---------------------------------------------------------------------------
# Bridge integration — real socket pair + real wsproto client
# ---------------------------------------------------------------------------


def _open_ws_pair(session_id: str = "test-id"):
    """Spawn a /bin/cat pty session + a socketpair + drive the WS
    handshake so both sides start in OPEN state. Returns
    (mgr, session, server_sock, client_sock, server_ws, client_ws)."""
    mgr = SessionManager()
    session = mgr.create(
        owner_email="t@x", host_alias="cat-test", ssh_user="x",
        address="local", executable="/bin/cat", args=[],
    )
    server_sock, client_sock = socket.socketpair()

    # Client initiates the WS upgrade.
    client_ws = WSConnection(ConnectionType.CLIENT)
    upgrade_bytes = client_ws.send(Request(
        host="localhost", target=f"/terminal/sessions/{session_id}/ws"
    ))

    # Server consumes the request bytes via wsproto in the same way
    # perform_ws_handshake does (we skip reconstructing requestline/
    # headers here since we're feeding raw wire bytes anyway).
    server_ws = WSConnection(ConnectionType.SERVER)
    server_ws.receive_data(upgrade_bytes)
    # Pull the Request event so we can respond.
    next((e for e in server_ws.events() if isinstance(e, Request)), None)
    server_sock.sendall(server_ws.send(AcceptConnection()))

    # Client consumes the 101 response → enters OPEN.
    client_sock.settimeout(2.0)
    client_ws.receive_data(client_sock.recv(4096))
    list(client_ws.events())  # drain AcceptConnection event

    return mgr, session, server_sock, client_sock, server_ws, client_ws


def _send_event(sock, ws, event) -> None:
    sock.sendall(ws.send(event))


def _recv_events(sock, ws, timeout=2.0):
    """Read until at least one event surfaces. Returns the list of
    events (might be more than one if data is bursty)."""
    sock.settimeout(timeout)
    while True:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            return []
        if not data:
            return []
        ws.receive_data(data)
        events = list(ws.events())
        if events:
            return events


def test_bridge_replays_scrollback_on_connect():
    """A reconnecting client first sees whatever scrollback was buffered."""
    mgr, session, server_sock, client_sock, server_ws, client_ws = _open_ws_pair()
    try:
        # Seed scrollback before the bridge starts (simulates a prior session).
        session._record_output(b"OLD BANNER\n")  # noqa: SLF001

        bridge_thread = threading.Thread(
            target=bridge_session_to_socket,
            kwargs=dict(session=session, manager=mgr, sock=server_sock,
                        ws=server_ws, stop_after_seconds=2.0),
            daemon=True,
        )
        bridge_thread.start()

        events = _recv_events(client_sock, client_ws, timeout=2.0)
        # First inbound frame is the scrollback replay.
        binary = next((e for e in events if isinstance(e, BytesMessage)), None)
        assert binary is not None
        assert b"OLD BANNER" in binary.data

        _send_event(client_sock, client_ws,
                    CloseConnection(code=1000, reason="bye"))
        bridge_thread.join(timeout=2.0)
    finally:
        client_sock.close()
        server_sock.close()
        mgr.shutdown_all()


def test_bridge_forwards_keystrokes_into_pty_and_streams_output_back():
    mgr, session, server_sock, client_sock, server_ws, client_ws = _open_ws_pair()
    try:
        bridge_thread = threading.Thread(
            target=bridge_session_to_socket,
            kwargs=dict(session=session, manager=mgr, sock=server_sock,
                        ws=server_ws, stop_after_seconds=3.0),
            daemon=True,
        )
        bridge_thread.start()

        _recv_events(client_sock, client_ws, timeout=0.3)

        # Send "hello\n" — cat echoes it back through the pty.
        _send_event(client_sock, client_ws, BytesMessage(data=b"hello\n"))

        seen = bytearray()
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline and b"hello" not in seen:
            for ev in _recv_events(client_sock, client_ws, timeout=0.5):
                if isinstance(ev, BytesMessage):
                    seen.extend(ev.data)
        assert b"hello" in bytes(seen)

        _send_event(client_sock, client_ws,
                    CloseConnection(code=1000, reason="bye"))
        bridge_thread.join(timeout=2.0)
    finally:
        client_sock.close()
        server_sock.close()
        mgr.shutdown_all()


def test_bridge_marks_session_attached_during_lifetime_then_detached():
    mgr, session, server_sock, client_sock, server_ws, client_ws = _open_ws_pair()
    try:
        assert session.attached is False

        bridge_thread = threading.Thread(
            target=bridge_session_to_socket,
            kwargs=dict(session=session, manager=mgr, sock=server_sock,
                        ws=server_ws, stop_after_seconds=1.5),
            daemon=True,
        )
        bridge_thread.start()

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not session.attached:
            time.sleep(0.02)
        assert session.attached is True

        _send_event(client_sock, client_ws,
                    CloseConnection(code=1000, reason="bye"))
        bridge_thread.join(timeout=2.0)
        assert session.attached is False
        assert session.last_detached_at is not None
    finally:
        client_sock.close()
        server_sock.close()
        mgr.shutdown_all()
