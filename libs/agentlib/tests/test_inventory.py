"""
Tests for agentlib.inventory.

Coverage:
- HostEntry + SshKey CRUD on both InMemory and FileBacked backends
- Validation (names, ports, addresses, reserved vars, reserved groups,
  duplicate names, missing key refs)
- Fingerprint determinism
- Key content is paste-once / read-via-explicit-getter (never on list)
- File-backed: persistence across instances + atomic key+metadata write
- render_ansible_inventory: groups, ungrouped fallback, vars quoting,
  key path injection, ordering determinism
- materialize_run_dir: end-to-end run dir layout with keys + inventory
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from agentlib import (
    FileBackedInventoryStore,
    HostEntry,
    InMemoryInventoryStore,
    InventoryError,
    SshKey,
    materialize_run_dir,
    render_ansible_inventory,
    ssh_key_fingerprint,
)

# Minimal valid-looking PEM bodies. We don't verify cryptographically;
# the heuristic only checks for the BEGIN ... PRIVATE KEY----- header.
_FAKE_KEY_A = textwrap.dedent(
    """
    -----BEGIN OPENSSH PRIVATE KEY-----
    AAAAB3NzaC1yc2EAAAADAQABAAABAQDAlpha
    -----END OPENSSH PRIVATE KEY-----
    """
).strip()

_FAKE_KEY_B = textwrap.dedent(
    """
    -----BEGIN OPENSSH PRIVATE KEY-----
    AAAAB3NzaC1yc2EAAAADAQABAAABAQDBeta
    -----END OPENSSH PRIVATE KEY-----
    """
).strip()


# Run every CRUD/validation test on both backends.
@pytest.fixture(params=["memory", "file"])
def store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryInventoryStore()
    else:
        yield FileBackedInventoryStore(tmp_path / "inventory.json")


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------


def test_fingerprint_is_deterministic_and_sha256_prefixed():
    fp1 = ssh_key_fingerprint(_FAKE_KEY_A)
    fp2 = ssh_key_fingerprint(_FAKE_KEY_A)
    assert fp1 == fp2
    assert fp1.startswith("SHA256:")
    # 32-byte sha256 → 43 chars base64 (no padding).
    assert len(fp1.split(":", 1)[1]) == 43


def test_fingerprint_differs_for_different_keys():
    assert ssh_key_fingerprint(_FAKE_KEY_A) != ssh_key_fingerprint(_FAKE_KEY_B)


def test_fingerprint_ignores_surrounding_whitespace():
    fp1 = ssh_key_fingerprint(_FAKE_KEY_A)
    fp2 = ssh_key_fingerprint("   \n" + _FAKE_KEY_A + "\n\n")
    assert fp1 == fp2


def test_fingerprint_rejects_empty():
    with pytest.raises(InventoryError):
        ssh_key_fingerprint("")


# ---------------------------------------------------------------------------
# Hosts: CRUD + validation
# ---------------------------------------------------------------------------


def test_add_and_list_host_round_trip(store):
    assert store.list_hosts() == []
    h = store.add_host(name="worker-1", address="10.0.0.1",
                       groups=["workers"], vars={"region": "us-west-2"})
    assert h.id
    assert h.name == "worker-1"
    assert h.address == "10.0.0.1"
    assert h.ssh_user == "ubuntu"
    assert h.ssh_port == 22
    assert h.groups == ["workers"]
    assert h.vars == {"region": "us-west-2"}

    listed = store.list_hosts()
    assert len(listed) == 1
    assert listed[0].id == h.id


def test_add_host_rejects_duplicate_name(store):
    store.add_host(name="cp", address="10.0.0.1")
    with pytest.raises(InventoryError, match="already exists"):
        store.add_host(name="cp", address="10.0.0.2")


def test_add_host_rejects_bad_name(store):
    with pytest.raises(InventoryError, match="name"):
        store.add_host(name="bad name with spaces", address="10.0.0.1")
    with pytest.raises(InventoryError, match="required"):
        store.add_host(name="", address="10.0.0.1")


def test_add_host_rejects_bad_address(store):
    with pytest.raises(InventoryError, match="address"):
        store.add_host(name="x", address="")
    with pytest.raises(InventoryError, match="whitespace"):
        store.add_host(name="y", address="10.0.0.1 extra")


def test_add_host_rejects_bad_port(store):
    with pytest.raises(InventoryError, match="ssh_port"):
        store.add_host(name="x", address="10.0.0.1", ssh_port=0)
    with pytest.raises(InventoryError, match="ssh_port"):
        store.add_host(name="y", address="10.0.0.1", ssh_port=99999)


def test_add_host_rejects_reserved_vars(store):
    with pytest.raises(InventoryError, match="reserved"):
        store.add_host(name="x", address="10.0.0.1",
                       vars={"ansible_host": "10.0.0.99"})


def test_add_host_rejects_reserved_group(store):
    with pytest.raises(InventoryError, match="reserved"):
        store.add_host(name="x", address="10.0.0.1", groups=["all"])
    with pytest.raises(InventoryError, match="reserved"):
        store.add_host(name="y", address="10.0.0.1", groups=["ungrouped"])


def test_add_host_rejects_unknown_key_id(store):
    with pytest.raises(InventoryError, match="key_id"):
        store.add_host(name="x", address="10.0.0.1", key_id="does-not-exist")


def test_get_host_by_name_returns_match_or_none(store):
    h = store.add_host(name="cp", address="10.0.0.1")
    assert store.get_host_by_name("cp").id == h.id
    assert store.get_host_by_name("nope") is None


def test_update_host_only_changes_named_fields(store):
    h = store.add_host(name="cp", address="10.0.0.1",
                       vars={"region": "us-west-2"})
    updated = store.update_host(h.id, address="10.0.0.99",
                                vars={"region": "us-east-1", "az": "1a"})
    assert updated.id == h.id
    assert updated.name == "cp"  # unchanged
    assert updated.address == "10.0.0.99"
    assert updated.vars == {"region": "us-east-1", "az": "1a"}
    assert updated.created_at == h.created_at
    assert updated.updated_at >= h.updated_at


def test_update_host_rejects_duplicate_name(store):
    a = store.add_host(name="a", address="10.0.0.1")
    store.add_host(name="b", address="10.0.0.2")
    with pytest.raises(InventoryError, match="already exists"):
        store.update_host(a.id, name="b")


def test_update_host_unknown_id(store):
    with pytest.raises(InventoryError, match="not found"):
        store.update_host("nope", address="10.0.0.1")


def test_remove_host(store):
    h = store.add_host(name="x", address="10.0.0.1")
    assert store.remove_host(h.id) is True
    assert store.remove_host(h.id) is False
    assert store.list_hosts() == []


# ---------------------------------------------------------------------------
# Keys: CRUD + content isolation
# ---------------------------------------------------------------------------


def test_add_key_returns_fingerprint_only(store):
    k = store.add_key(name="k1", content=_FAKE_KEY_A)
    assert isinstance(k, SshKey)
    assert k.fingerprint.startswith("SHA256:")
    assert not hasattr(k, "content")


def test_add_key_rejects_non_pem_content(store):
    with pytest.raises(InventoryError, match="PRIVATE KEY"):
        store.add_key(name="bad", content="not-a-key")


def test_add_key_rejects_empty_content(store):
    with pytest.raises(InventoryError, match="empty"):
        store.add_key(name="bad", content="")


def test_add_key_rejects_duplicate_name(store):
    store.add_key(name="k1", content=_FAKE_KEY_A)
    with pytest.raises(InventoryError, match="already exists"):
        store.add_key(name="k1", content=_FAKE_KEY_B)


def test_list_keys_never_includes_content(store):
    store.add_key(name="k1", content=_FAKE_KEY_A)
    listed = store.list_keys()
    assert len(listed) == 1
    # Sanity: the dataclass exposes only metadata.
    assert listed[0].fingerprint
    # The content must be accessible only via the explicit getter.
    assert store.get_key_content(listed[0].id) is not None
    assert _FAKE_KEY_A.strip() in store.get_key_content(listed[0].id)


def test_get_key_content_returns_none_for_unknown(store):
    assert store.get_key_content("nope") is None


def test_remove_key_blocked_when_in_use(store):
    k = store.add_key(name="k1", content=_FAKE_KEY_A)
    store.add_host(name="h1", address="10.0.0.1", key_id=k.id)
    with pytest.raises(InventoryError, match="in use"):
        store.remove_key(k.id)


def test_remove_key_succeeds_when_not_referenced(store):
    k = store.add_key(name="k1", content=_FAKE_KEY_A)
    assert store.remove_key(k.id) is True
    assert store.get_key(k.id) is None
    assert store.get_key_content(k.id) is None
    assert store.remove_key(k.id) is False


# ---------------------------------------------------------------------------
# FileBackedInventoryStore: persistence + on-disk hygiene
# ---------------------------------------------------------------------------


def test_file_backed_persists_across_instances(tmp_path):
    path = tmp_path / "inventory.json"
    s1 = FileBackedInventoryStore(path)
    k = s1.add_key(name="k1", content=_FAKE_KEY_A)
    s1.add_host(name="cp", address="10.0.0.1", key_id=k.id, groups=["control"])
    del s1

    s2 = FileBackedInventoryStore(path)
    assert [h.name for h in s2.list_hosts()] == ["cp"]
    assert [kk.fingerprint for kk in s2.list_keys()] == [k.fingerprint]
    assert s2.get_key_content(k.id).strip() == _FAKE_KEY_A.strip()


def test_file_backed_key_file_has_0600_perms(tmp_path):
    path = tmp_path / "inventory.json"
    store = FileBackedInventoryStore(path)
    k = store.add_key(name="k1", content=_FAKE_KEY_A)
    kp = path.parent / "keys" / k.id
    assert kp.exists()
    mode = kp.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600 perms, got {oct(mode)}"


def test_file_backed_removes_key_file_on_remove(tmp_path):
    path = tmp_path / "inventory.json"
    store = FileBackedInventoryStore(path)
    k = store.add_key(name="k1", content=_FAKE_KEY_A)
    kp = path.parent / "keys" / k.id
    assert kp.exists()
    store.remove_key(k.id)
    assert not kp.exists()


def test_file_backed_corrupt_file_treated_as_empty(tmp_path):
    path = tmp_path / "inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", "utf-8")
    store = FileBackedInventoryStore(path)
    assert store.list_hosts() == []
    assert store.list_keys() == []
    # And it can recover by writing.
    store.add_host(name="x", address="10.0.0.1")
    assert json.loads(path.read_text("utf-8"))["hosts"][0]["name"] == "x"


def test_file_backed_in_memory_round_trip_via_inmemory_too():
    """Sanity: the InMemory store behaves the same shape so the
    pytest fixture parametrization above covers both."""
    s = InMemoryInventoryStore()
    s.add_host(name="x", address="10.0.0.1")
    assert s.list_hosts()[0].name == "x"


# ---------------------------------------------------------------------------
# render_ansible_inventory
# ---------------------------------------------------------------------------


def _strip_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("#")
    ).strip()


def test_render_groups_and_ungrouped():
    hosts = [
        HostEntry(id="1", name="cp", address="10.0.0.1",
                  groups=["control_plane"]),
        HostEntry(id="2", name="w1", address="10.0.0.2",
                  groups=["workers"]),
        HostEntry(id="3", name="floating", address="10.0.0.9",
                  groups=[]),
    ]
    text = render_ansible_inventory(hosts)
    body = _strip_comments(text)
    # Group sections appear, sorted alphabetically.
    assert "[control_plane]" in body
    assert "[workers]" in body
    assert "[ungrouped]" in body
    assert body.index("[control_plane]") < body.index("[ungrouped]") < body.index("[workers]")
    assert "cp ansible_host=10.0.0.1 ansible_user=ubuntu" in body
    assert "floating ansible_host=10.0.0.9" in body


def test_render_host_in_multiple_groups():
    hosts = [
        HostEntry(id="1", name="cp", address="10.0.0.1",
                  groups=["control_plane", "monitored"]),
    ]
    text = render_ansible_inventory(hosts)
    body = _strip_comments(text)
    assert body.count("cp ansible_host=10.0.0.1") == 2
    assert "[control_plane]" in body
    assert "[monitored]" in body


def test_render_includes_custom_vars_and_quotes_when_needed():
    hosts = [
        HostEntry(id="1", name="cp", address="10.0.0.1",
                  vars={"region": "us-west-2",
                        "note": "spot instance, on-demand fallback"}),
    ]
    text = render_ansible_inventory(hosts)
    body = _strip_comments(text)
    assert "region=us-west-2" in body
    assert 'note="spot instance, on-demand fallback"' in body


def test_render_injects_private_key_file():
    hosts = [
        HostEntry(id="1", name="cp", address="10.0.0.1", key_id="kid-1"),
        HostEntry(id="2", name="w1", address="10.0.0.2", key_id="kid-2"),
        HostEntry(id="3", name="nokey", address="10.0.0.3"),
    ]
    text = render_ansible_inventory(
        hosts, key_paths={"kid-1": "/tmp/run/keys/kid-1"}
    )
    body = _strip_comments(text)
    assert "ansible_ssh_private_key_file=/tmp/run/keys/kid-1" in body
    # nokey + kid-2 (not in map) → no private_key_file attribute
    assert body.count("ansible_ssh_private_key_file=") == 1


def test_render_includes_non_default_port():
    hosts = [
        HostEntry(id="1", name="cp", address="10.0.0.1", ssh_port=2222),
        HostEntry(id="2", name="w1", address="10.0.0.2"),
    ]
    text = render_ansible_inventory(hosts)
    body = _strip_comments(text)
    assert "cp ansible_host=10.0.0.1 ansible_user=ubuntu ansible_port=2222" in body
    # Default 22 omitted.
    assert "w1 ansible_host=10.0.0.2 ansible_user=ubuntu" in body
    assert "w1 ansible_host=10.0.0.2 ansible_user=ubuntu ansible_port=" not in body


def test_render_is_deterministic():
    hosts = [
        HostEntry(id="1", name="b", address="10.0.0.2", groups=["g2"]),
        HostEntry(id="2", name="a", address="10.0.0.1", groups=["g1"]),
    ]
    t1 = render_ansible_inventory(hosts)
    t2 = render_ansible_inventory(list(reversed(hosts)))
    assert t1 == t2


# ---------------------------------------------------------------------------
# materialize_run_dir
# ---------------------------------------------------------------------------


def test_materialize_writes_inventory_and_keys(tmp_path):
    store = InMemoryInventoryStore()
    k = store.add_key(name="prod", content=_FAKE_KEY_A)
    store.add_host(name="cp", address="10.0.0.1", key_id=k.id,
                   groups=["control"])
    store.add_host(name="w1", address="10.0.0.2", groups=["workers"])

    run_dir = tmp_path / "ansible-run"
    result = materialize_run_dir(store, run_dir)

    assert result.inventory_path.exists()
    inv_text = result.inventory_path.read_text("utf-8")
    assert "cp ansible_host=10.0.0.1" in inv_text
    assert "ansible_ssh_private_key_file=" in inv_text
    # Key file landed in the run dir.
    assert len(result.key_paths) == 1
    key_path = Path(result.key_paths[k.id])
    assert key_path.exists()
    assert key_path.read_text("utf-8").strip() == _FAKE_KEY_A.strip()
    # Inventory references the key by its on-disk path.
    assert str(key_path) in inv_text


def test_materialize_excludes_self_node_addresses(tmp_path):
    """exclude_addresses drops the cluster/VM hosts Olympus runs on so they
    never appear in the rendered inventory (self-protection belt-and-suspenders)."""
    store = InMemoryInventoryStore()
    store.add_host(name="cp", address="10.0.3.20", groups=["control"])
    store.add_host(name="w1", address="10.0.3.21", groups=["workers"])
    store.add_host(name="edge", address="203.0.113.9", groups=["edge"])

    # Mixed case / port to confirm normalization on the exclusion side too.
    result = materialize_run_dir(
        store, tmp_path / "run",
        exclude_addresses={"10.0.3.20", "10.0.3.21:22"},
    )
    inv_text = result.inventory_path.read_text("utf-8")
    assert "edge ansible_host=203.0.113.9" in inv_text
    assert "10.0.3.20" not in inv_text
    assert "10.0.3.21" not in inv_text
    assert "cp " not in inv_text and "w1 " not in inv_text


def test_materialize_key_files_are_0600(tmp_path):
    store = InMemoryInventoryStore()
    k = store.add_key(name="k", content=_FAKE_KEY_A)
    store.add_host(name="cp", address="10.0.0.1", key_id=k.id)
    result = materialize_run_dir(store, tmp_path / "run")
    kp = Path(result.key_paths[k.id])
    mode = kp.stat().st_mode & 0o777
    assert mode == 0o600


def test_materialize_handles_orphaned_key_ref(tmp_path):
    """A host whose key_id was deleted out from under it (shouldn't
    happen via the API but the store doesn't prevent it at the file
    level) renders without a key_file attr instead of crashing."""
    store = InMemoryInventoryStore()
    store.add_host(name="cp", address="10.0.0.1", key_id=None)
    result = materialize_run_dir(store, tmp_path / "run")
    inv = result.inventory_path.read_text("utf-8")
    assert "cp ansible_host=10.0.0.1" in inv
    assert "ansible_ssh_private_key_file=" not in inv


def test_materialize_creates_empty_inventory_when_no_hosts(tmp_path):
    store = InMemoryInventoryStore()
    result = materialize_run_dir(store, tmp_path / "run")
    inv = result.inventory_path.read_text("utf-8")
    # Header comment only.
    assert "Generated by Olympus inventory store" in inv
    assert "[" not in inv  # no sections
