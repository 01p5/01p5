"""
Unit tests for olympus_cli.inventory_screen.

Same pattern as test_tui_unit.py: skip cleanly when textual isn't
installed; exercise the synchronous bits (constructor, _reload_*
data-row building, action_refresh dispatch) by mocking ``query_one``
so we don't need to spin up a live Textual App. The actual widget
rendering is Textual's concern, not ours.
"""
from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch

import pytest

textual = pytest.importorskip("textual")  # noqa: F841

from agentlib import InMemoryInventoryStore  # noqa: E402

from olympus_cli.inventory_screen import (  # noqa: E402
    DEFAULT_STORE,
    InventoryScreen,
)


_FAKE_KEY = textwrap.dedent(
    """
    -----BEGIN OPENSSH PRIVATE KEY-----
    AAAAB3NzaC1yc2EAAAADAQABAAABAQDscreen
    -----END OPENSSH PRIVATE KEY-----
    """
).strip()


def _populated_store() -> InMemoryInventoryStore:
    s = InMemoryInventoryStore()
    k = s.add_key(name="cluster", content=_FAKE_KEY)
    s.add_host(name="cp", address="10.0.0.1", key_id=k.id,
               groups=["control_plane"], vars={"region": "us-west-2"})
    s.add_host(name="w1", address="10.0.0.2", key_id=k.id,
               groups=["workers"])
    return s


def test_constructor_uses_explicit_store_when_provided():
    store = _populated_store()
    screen = InventoryScreen(store=store)
    assert screen._store is store


def test_constructor_defaults_to_file_backed_store_at_default_path():
    # Don't actually touch ~/.olympus — just verify the seam: when no
    # store is passed in, the screen instantiates a FileBackedInventoryStore
    # using the provided store_path.
    with patch("olympus_cli.inventory_screen.FileBackedInventoryStore") as fake_cls:
        InventoryScreen(store_path=DEFAULT_STORE)
    fake_cls.assert_called_once()
    # First (positional) arg should be the expanded default path.
    arg = fake_cls.call_args.args[0]
    assert ".olympus/inventory.json" in arg
    # Path is expanded — no leading ~.
    assert not arg.startswith("~")


def test_constructor_accepts_custom_store_path():
    with patch("olympus_cli.inventory_screen.FileBackedInventoryStore") as fake_cls:
        InventoryScreen(store_path="/tmp/custom-inventory.json")
    assert fake_cls.call_args.args[0] == "/tmp/custom-inventory.json"


def test_bindings_include_quit_and_refresh():
    binding_keys = {b.key for b in InventoryScreen.BINDINGS}
    assert "q" in binding_keys
    assert "r" in binding_keys


def _make_screen_with_stubbed_widgets(store):
    """Build a screen whose ``query_one`` returns a fresh MagicMock per call
    so the _reload_* methods can drive .clear / .add_columns / .add_row /
    .update without needing a mounted Textual App."""
    screen = InventoryScreen(store=store)
    widgets: dict[str, MagicMock] = {}

    def fake_query_one(selector, *args, **kwargs):
        return widgets.setdefault(selector, MagicMock(name=selector))

    screen.query_one = fake_query_one  # type: ignore[assignment]
    return screen, widgets


def test_reload_hosts_populates_table_one_row_per_host():
    screen, widgets = _make_screen_with_stubbed_widgets(_populated_store())
    screen._reload_hosts()
    table = widgets["#host-table"]
    table.clear.assert_called_once_with(columns=True)
    table.add_columns.assert_called_once()
    assert table.add_row.call_count == 2  # cp + w1
    # First column of each row is the host name.
    names = [call.args[0] for call in table.add_row.call_args_list]
    assert set(names) == {"cp", "w1"}


def test_reload_hosts_renders_default_group_dash_for_ungrouped_hosts():
    store = InMemoryInventoryStore()
    store.add_host(name="floating", address="10.0.0.9")
    screen, widgets = _make_screen_with_stubbed_widgets(store)
    screen._reload_hosts()
    row = widgets["#host-table"].add_row.call_args_list[0].args
    # groups column index = 4 (name, address, ssh_user, port, groups, key, vars)
    assert row[4] == "-"


def test_reload_hosts_renders_key_short_id_when_present():
    store = _populated_store()
    screen, widgets = _make_screen_with_stubbed_widgets(store)
    screen._reload_hosts()
    # cp + w1 both reference the same key — its short id appears in
    # both rows. Take the first row's key column (index 5).
    first_row_key = widgets["#host-table"].add_row.call_args_list[0].args[5]
    assert first_row_key != "-"
    assert "…" in first_row_key  # truncated short id


def test_reload_keys_populates_table_one_row_per_key():
    screen, widgets = _make_screen_with_stubbed_widgets(_populated_store())
    screen._reload_keys()
    table = widgets["#key-table"]
    table.clear.assert_called_once_with(columns=True)
    table.add_columns.assert_called_once_with("name", "fingerprint", "id")
    assert table.add_row.call_count == 1
    name, fingerprint, _id = table.add_row.call_args.args
    assert name == "cluster"
    assert fingerprint.startswith("SHA256:")


def test_reload_rendered_updates_with_inventory_ini():
    screen, widgets = _make_screen_with_stubbed_widgets(_populated_store())
    screen._reload_rendered()
    static = widgets["#rendered"]
    static.update.assert_called_once()
    text = static.update.call_args.args[0]
    assert "[control_plane]" in text
    assert "[workers]" in text
    assert "cp ansible_host=10.0.0.1" in text


def test_reload_rendered_empty_store_shows_helpful_placeholder():
    screen, widgets = _make_screen_with_stubbed_widgets(InMemoryInventoryStore())
    screen._reload_rendered()
    text = widgets["#rendered"].update.call_args.args[0]
    assert "empty inventory" in text


def test_action_refresh_reloads_all_three_views():
    screen, widgets = _make_screen_with_stubbed_widgets(_populated_store())
    screen.action_refresh()
    # All three widgets got touched.
    assert "#host-table" in widgets
    assert "#key-table" in widgets
    assert "#rendered" in widgets


def test_on_mount_reloads_all_three_views():
    screen, widgets = _make_screen_with_stubbed_widgets(_populated_store())
    screen.on_mount()
    assert "#host-table" in widgets
    assert "#key-table" in widgets
    assert "#rendered" in widgets
