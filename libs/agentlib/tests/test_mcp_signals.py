"""
Phase 4 — MCP push signals.

A server-pushed notification, drained by MCPSignalReader, becomes an
``mcp_event`` BusMessage (visible on the live stream + audited in the bus
log) and projects into a ticket transcript via the ticket sink.
"""
from __future__ import annotations

import time

from agentlib import (
    InMemoryBus,
    InMemoryTicketStore,
    MCPSignalReader,
    MockTransport,
    event_from_bus,
    new_message,
    ticket_bus_sink,
)


def _ok_handler(_msg):
    return {"jsonrpc": "2.0", "id": _msg.get("id"), "result": {}}


def test_mcp_event_bus_kind_projects_to_transcript():
    # event_from_bus maps the new mcp_event bus kind onto the transcript.
    # ticket_id is required (the sink now skips messages without it); the
    # MCPSignalReader sets ticket_id=server_name so each server gets a
    # natural per-server "ticket".
    msg = new_message("slurm-mcp", "slurm-mcp", "*", "mcp_event",
                      {"method": "notifications/resources/updated"},
                      ticket_id="slurm-mcp")
    ev = event_from_bus(msg)
    assert ev is not None
    assert ev.kind == "mcp_event"
    assert ev.actor == "slurm-mcp"
    assert ev.ticket_id == "slurm-mcp"


def test_poll_once_publishes_pushed_notifications():
    bus = InMemoryBus()
    transport = MockTransport(_ok_handler)
    reader = MCPSignalReader(transport, bus, server_name="gpu-mcp")

    transport.push_server_notification({"method": "notifications/message", "params": {"level": "warning", "data": "ECC errors on node-2"}})
    transport.push_server_notification({"method": "notifications/progress", "params": {"progress": 0.5}})

    published = reader.poll_once()
    assert published == 2

    mcp_msgs = [m for m in bus.log if m.kind == "mcp_event"]
    assert len(mcp_msgs) == 2
    assert all(m.sender == "gpu-mcp" for m in mcp_msgs)
    assert mcp_msgs[0].payload["params"]["data"] == "ECC errors on node-2"
    # Draining is one-shot — a second poll finds nothing new.
    assert reader.poll_once() == 0


def test_pushed_notification_lands_in_ticket_transcript():
    bus = InMemoryBus()
    store = InMemoryTicketStore()
    bus.subscribe("*", ticket_bus_sink(store))
    transport = MockTransport(_ok_handler)
    reader = MCPSignalReader(transport, bus, server_name="gpu-mcp")

    transport.push_server_notification({"method": "notifications/message", "params": {"data": "node draining"}})
    reader.poll_once()

    # task_id falls back to the ticket key; the sink recorded an mcp_event.
    events = store.transcript("gpu-mcp")
    assert len(events) == 1
    assert events[0].kind == "mcp_event"
    assert events[0].actor == "gpu-mcp"


def test_poll_once_noop_when_transport_lacks_push():
    bus = InMemoryBus()

    class _NoPush:
        def send(self, m, timeout=30.0): return {}
        def notify(self, m): ...
        def close(self): ...

    reader = MCPSignalReader(_NoPush(), bus, server_name="x")
    assert reader.poll_once() == 0
    assert bus.log == []


def test_start_stop_drains_in_background():
    bus = InMemoryBus()
    transport = MockTransport(_ok_handler)
    reader = MCPSignalReader(transport, bus, server_name="gpu-mcp", poll_interval=0.02)
    reader.start()
    try:
        transport.push_server_notification({"method": "notifications/message", "params": {"data": "hi"}})
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(m.kind == "mcp_event" for m in bus.log):
            time.sleep(0.02)
    finally:
        reader.stop()
    assert any(m.kind == "mcp_event" for m in bus.log)
    # Idempotent stop.
    reader.stop()
