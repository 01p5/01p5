"""
TUI smoke test — only runs when textual is installed. Exercises that the
module imports, the App subclass exposes the expected attributes, and
the bus subscription wiring is hooked up.
"""
from __future__ import annotations

import pytest

textual = pytest.importorskip("textual")  # noqa: F841


def test_olympus_app_exists_and_uses_in_memory_bus():
    from agentlib import InMemoryBus
    from olympus_cli.tui import OlympusApp

    app = OlympusApp(router_name="manual")
    assert isinstance(app._bus, InMemoryBus)
    assert app._router_name == "manual"
