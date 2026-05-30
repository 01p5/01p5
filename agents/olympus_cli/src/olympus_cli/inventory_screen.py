"""
Textual screen for inventory inspection inside the Olympus TUI.

The minimum viable shape: a read-only browser for hosts + keys + the
rendered ansible inventory. Mutations go through ``olympus-inventory``
or the webui Hosts tab — keeping write paths in one place avoids
two implementations of the same form validation.

Bound to ``i`` in the main TUI app; press ``q`` to return to the main
screen. Textual is an optional dependency; the screen import fails
loud with the same error message the rest of the TUI does.
"""
from __future__ import annotations

import os
from typing import Optional

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.screen import Screen
    from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane
except ImportError as exc:  # pragma: no cover — exercised at runtime, not in tests
    raise ImportError(
        "olympus_cli.inventory_screen requires the optional 'textual' "
        "dependency. Install with `pip install textual`."
    ) from exc

from agentlib import (
    FileBackedInventoryStore,
    InventoryStore,
    render_ansible_inventory,
)

DEFAULT_STORE = "~/.olympus/inventory.json"


class InventoryScreen(Screen):
    """Three-tab inventory browser (hosts / keys / rendered inventory).

    ``r`` refreshes from disk (the file-backed store re-reads on every
    call so this is a one-liner). Edits via olympus-inventory or webui
    will show up on the next refresh.
    """

    BINDINGS = [
        Binding("q", "app.pop_screen", "back"),
        Binding("r", "refresh", "refresh"),
    ]

    CSS = """
    DataTable { height: 1fr; }
    #rendered { padding: 1 2; }
    """

    def __init__(self, store: Optional[InventoryStore] = None,
                 store_path: str = DEFAULT_STORE):
        super().__init__()
        self._store = store or FileBackedInventoryStore(os.path.expanduser(store_path))
        self._store_path = store_path

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="hosts"):
            with TabPane("Hosts", id="hosts"):
                yield DataTable(id="host-table", cursor_type="row")
            with TabPane("Keys", id="keys"):
                yield DataTable(id="key-table", cursor_type="row")
            with TabPane("Rendered inventory.ini", id="rendered-tab"):
                yield Vertical(Static(id="rendered"))
        yield Footer()

    def on_mount(self) -> None:
        self._reload_hosts()
        self._reload_keys()
        self._reload_rendered()

    def action_refresh(self) -> None:
        self._reload_hosts()
        self._reload_keys()
        self._reload_rendered()

    def _reload_hosts(self) -> None:
        table = self.query_one("#host-table", DataTable)
        table.clear(columns=True)
        table.add_columns("name", "address", "ssh_user", "port", "groups", "key", "vars")
        for h in self._store.list_hosts():
            table.add_row(
                h.name, h.address, h.ssh_user, str(h.ssh_port),
                ",".join(h.groups) or "-",
                (h.key_id[:8] + "…") if h.key_id else "-",
                ",".join(f"{k}={v}" for k, v in sorted(h.vars.items())) or "-",
            )

    def _reload_keys(self) -> None:
        table = self.query_one("#key-table", DataTable)
        table.clear(columns=True)
        table.add_columns("name", "fingerprint", "id")
        for k in self._store.list_keys():
            table.add_row(k.name, k.fingerprint, k.id)

    def _reload_rendered(self) -> None:
        static = self.query_one("#rendered", Static)
        text = render_ansible_inventory(self._store.list_hosts())
        static.update(text or "(empty inventory — add hosts via olympus-inventory or the webui)")
