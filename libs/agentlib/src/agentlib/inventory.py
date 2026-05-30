"""
Inventory layer — user-managed hosts + ssh keys for the ansible / sysadmin agents.

The dashboard surfaces a "Hosts" tab where authenticated users add hosts
(name + address + ssh_user + ssh_port + groups + custom vars) and upload
ssh keys (paste private key; we store + fingerprint; never return content
via the public API). The ansible agent renders an ``inventory.ini`` from
this store at run time; the sysadmin agent's new ``ssh_run`` tool
resolves a host alias + key + runs ssh.

The olympus_cli TUI uses the same Protocol with a different backend
(file-backed at ``~/.olympus/inventory.json`` by default), so both
surfaces edit the same shape.

Two backends ship with v1:

- ``InMemoryInventoryStore`` — drop-in for tests and short-lived runs.
  Thread-safe. Key content held in-process; nothing touches disk.

- ``FileBackedInventoryStore`` — JSON metadata file on disk; key
  contents in a sibling ``keys/`` dir with 0600 perms. Right default
  for both the dashboard (mounted volume in k8s) and the TUI (under
  ``~/.olympus``).

Custom host attributes (``HostEntry.vars``) round-trip to ansible host
vars; ``HostEntry.groups`` round-trips to ansible inventory groups.
``render_ansible_inventory`` returns INI text the ansible agent can
write to a tempfile + point ANSIBLE_INVENTORY at.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Protocol

_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
_RESERVED_VARS = frozenset({
    "ansible_host",
    "ansible_user",
    "ansible_port",
    "ansible_ssh_private_key_file",
})
_RESERVED_GROUP_NAMES = frozenset({"all", "ungrouped"})


def _new_id() -> str:
    return uuid.uuid4().hex


def ssh_key_fingerprint(content: str | bytes) -> str:
    """Return an ssh-keygen-style SHA256 fingerprint of a key body.

    Computed over the canonicalized file bytes (not the public-key wire
    format). The UX value is "the same key always gets the same
    fingerprint" — that's what users compare against ``ssh-keygen -lf``.
    Format matches ssh-keygen's output: ``SHA256:<base64-no-padding>``.
    """
    if isinstance(content, str):
        body = content.strip().encode("utf-8")
    else:
        body = content.strip()
    if not body:
        raise InventoryError("ssh key content is empty")
    digest = hashlib.sha256(body).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _looks_like_private_key(content: str) -> bool:
    """Cheap heuristic — the body has a BEGIN/END PEM block.

    Doesn't validate cryptographic correctness (no openssl / cryptography
    dep). Just rejects obvious paste mistakes like the *public* key, a
    fingerprint, or random text.
    """
    head = content.strip().splitlines()[:1]
    return bool(head) and head[0].startswith("-----BEGIN ") and "PRIVATE KEY-----" in head[0]


@dataclass
class HostEntry:
    """One inventory host. ``id`` is auto-generated; ``name`` is the
    alias the agents use (unique + alphanumeric/._-). ``vars`` carries
    user-defined attributes that become ansible host vars; ``groups``
    decides which ansible inventory groups the host appears in.
    """

    id: str
    name: str
    address: str
    ssh_user: str = "ubuntu"
    ssh_port: int = 22
    key_id: Optional[str] = None
    groups: list[str] = field(default_factory=list)
    vars: dict[str, str] = field(default_factory=dict)
    description: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class SshKey:
    """One stored private key. ``content`` is *never* returned via the
    list_keys endpoint (UI sees id+name+fingerprint only). Callers that
    legitimately need the body (the ansible agent materializing the key
    to disk) go through ``InventoryStore.get_key_content`` explicitly.
    """

    id: str
    name: str
    fingerprint: str
    created_at: float = field(default_factory=time.time)


class InventoryError(ValueError):
    """User-facing inventory errors: duplicate names, missing keys,
    malformed input, reserved names. Surfaced as 400s by the dashboard."""


class InventoryStore(Protocol):
    # Hosts
    def list_hosts(self) -> list[HostEntry]: ...
    def get_host(self, host_id: str) -> Optional[HostEntry]: ...
    def get_host_by_name(self, name: str) -> Optional[HostEntry]: ...
    def add_host(
        self,
        *,
        name: str,
        address: str,
        ssh_user: str = "ubuntu",
        ssh_port: int = 22,
        key_id: Optional[str] = None,
        groups: Optional[list[str]] = None,
        vars: Optional[Mapping[str, Any]] = None,
        description: str = "",
    ) -> HostEntry: ...
    def update_host(self, host_id: str, **fields: Any) -> HostEntry: ...
    def remove_host(self, host_id: str) -> bool: ...
    # Keys
    def list_keys(self) -> list[SshKey]: ...
    def get_key(self, key_id: str) -> Optional[SshKey]: ...
    def add_key(self, *, name: str, content: str) -> SshKey: ...
    def get_key_content(self, key_id: str) -> Optional[str]: ...
    def remove_key(self, key_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_name(name: str, *, field_label: str = "name") -> str:
    n = (name or "").strip()
    if not n:
        raise InventoryError(f"{field_label} is required")
    if not _NAME_RE.match(n):
        raise InventoryError(
            f"{field_label} must contain only letters, digits, '.', '_', '-'"
        )
    return n


def _validate_groups(groups: Optional[list[str]]) -> list[str]:
    if not groups:
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    for g in groups:
        g = (g or "").strip()
        if not g:
            continue
        if g in _RESERVED_GROUP_NAMES:
            raise InventoryError(
                f"group name '{g}' is reserved (ansible adds it automatically)"
            )
        if not _NAME_RE.match(g):
            raise InventoryError(
                f"group name '{g}' must match [a-zA-Z0-9._-]+"
            )
        if g not in seen_set:
            seen.append(g)
            seen_set.add(g)
    return seen


def _validate_vars(raw: Optional[Mapping[str, Any]]) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = (k or "").strip()
        if not key:
            continue
        if not _NAME_RE.match(key):
            raise InventoryError(
                f"variable name '{key}' must match [a-zA-Z0-9._-]+"
            )
        if key in _RESERVED_VARS:
            raise InventoryError(
                f"variable '{key}' is reserved; use the structured field instead"
            )
        out[key] = "" if v is None else str(v)
    return out


def _validate_port(port: Any) -> int:
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise InventoryError("ssh_port must be an integer") from None
    if p < 1 or p > 65535:
        raise InventoryError("ssh_port must be in 1..65535")
    return p


def _validate_address(address: str) -> str:
    a = (address or "").strip()
    if not a:
        raise InventoryError("address is required")
    # Bare sanity check — anything with whitespace is wrong. Don't try
    # to validate IPs vs hostnames here; ssh will tell us if it's bogus.
    if any(c.isspace() for c in a):
        raise InventoryError("address must not contain whitespace")
    return a


# ---------------------------------------------------------------------------
# InMemoryInventoryStore
# ---------------------------------------------------------------------------


class InMemoryInventoryStore:
    """Process-local store. Thread-safe. Keys held in-memory only."""

    def __init__(self) -> None:
        self._hosts: dict[str, HostEntry] = {}
        self._keys: dict[str, SshKey] = {}
        self._key_contents: dict[str, str] = {}
        self._lock = threading.RLock()

    # ----- hosts -----

    def list_hosts(self) -> list[HostEntry]:
        with self._lock:
            return [
                HostEntry(**asdict(h)) for h in sorted(
                    self._hosts.values(), key=lambda h: h.name
                )
            ]

    def get_host(self, host_id: str) -> Optional[HostEntry]:
        with self._lock:
            h = self._hosts.get(host_id)
            return HostEntry(**asdict(h)) if h else None

    def get_host_by_name(self, name: str) -> Optional[HostEntry]:
        with self._lock:
            for h in self._hosts.values():
                if h.name == name:
                    return HostEntry(**asdict(h))
        return None

    def add_host(
        self,
        *,
        name: str,
        address: str,
        ssh_user: str = "ubuntu",
        ssh_port: int = 22,
        key_id: Optional[str] = None,
        groups: Optional[list[str]] = None,
        vars: Optional[Mapping[str, Any]] = None,
        description: str = "",
    ) -> HostEntry:
        n = _validate_name(name)
        addr = _validate_address(address)
        port = _validate_port(ssh_port)
        user = _validate_name(ssh_user, field_label="ssh_user")
        clean_groups = _validate_groups(groups)
        clean_vars = _validate_vars(vars)
        with self._lock:
            if any(h.name == n for h in self._hosts.values()):
                raise InventoryError(f"host with name '{n}' already exists")
            if key_id is not None and key_id not in self._keys:
                raise InventoryError(f"key_id '{key_id}' not found")
            now = time.time()
            entry = HostEntry(
                id=_new_id(),
                name=n,
                address=addr,
                ssh_user=user,
                ssh_port=port,
                key_id=key_id or None,
                groups=clean_groups,
                vars=clean_vars,
                description=(description or "").strip(),
                created_at=now,
                updated_at=now,
            )
            self._hosts[entry.id] = entry
            return HostEntry(**asdict(entry))

    def update_host(self, host_id: str, **fields: Any) -> HostEntry:
        with self._lock:
            existing = self._hosts.get(host_id)
            if existing is None:
                raise InventoryError(f"host '{host_id}' not found")

            current = asdict(existing)
            if "name" in fields:
                current["name"] = _validate_name(fields["name"])
                if any(
                    h.name == current["name"] and h.id != host_id
                    for h in self._hosts.values()
                ):
                    raise InventoryError(
                        f"host with name '{current['name']}' already exists"
                    )
            if "address" in fields:
                current["address"] = _validate_address(fields["address"])
            if "ssh_user" in fields:
                current["ssh_user"] = _validate_name(
                    fields["ssh_user"], field_label="ssh_user"
                )
            if "ssh_port" in fields:
                current["ssh_port"] = _validate_port(fields["ssh_port"])
            if "key_id" in fields:
                kid = fields["key_id"]
                if kid is not None and kid not in self._keys:
                    raise InventoryError(f"key_id '{kid}' not found")
                current["key_id"] = kid or None
            if "groups" in fields:
                current["groups"] = _validate_groups(fields["groups"])
            if "vars" in fields:
                current["vars"] = _validate_vars(fields["vars"])
            if "description" in fields:
                current["description"] = (fields["description"] or "").strip()
            current["updated_at"] = time.time()
            # id + created_at are immutable.
            current["id"] = existing.id
            current["created_at"] = existing.created_at
            new_entry = HostEntry(**current)
            self._hosts[host_id] = new_entry
            return HostEntry(**asdict(new_entry))

    def remove_host(self, host_id: str) -> bool:
        with self._lock:
            return self._hosts.pop(host_id, None) is not None

    # ----- keys -----

    def list_keys(self) -> list[SshKey]:
        with self._lock:
            return [
                SshKey(**asdict(k)) for k in sorted(
                    self._keys.values(), key=lambda k: k.name
                )
            ]

    def get_key(self, key_id: str) -> Optional[SshKey]:
        with self._lock:
            k = self._keys.get(key_id)
            return SshKey(**asdict(k)) if k else None

    def add_key(self, *, name: str, content: str) -> SshKey:
        n = _validate_name(name)
        body = (content or "").strip()
        if not body:
            raise InventoryError("key content is empty")
        if not _looks_like_private_key(body):
            raise InventoryError(
                "content does not look like a PEM private key "
                "(missing -----BEGIN ... PRIVATE KEY----- header)"
            )
        fp = ssh_key_fingerprint(body)
        with self._lock:
            if any(k.name == n for k in self._keys.values()):
                raise InventoryError(f"key with name '{n}' already exists")
            entry = SshKey(id=_new_id(), name=n, fingerprint=fp)
            self._keys[entry.id] = entry
            self._key_contents[entry.id] = body
            return SshKey(**asdict(entry))

    def get_key_content(self, key_id: str) -> Optional[str]:
        with self._lock:
            return self._key_contents.get(key_id)

    def remove_key(self, key_id: str) -> bool:
        with self._lock:
            if key_id not in self._keys:
                return False
            in_use = [h.name for h in self._hosts.values() if h.key_id == key_id]
            if in_use:
                raise InventoryError(
                    f"key is in use by host(s): {', '.join(in_use)}"
                )
            self._keys.pop(key_id, None)
            self._key_contents.pop(key_id, None)
            return True


# ---------------------------------------------------------------------------
# FileBackedInventoryStore
# ---------------------------------------------------------------------------


class FileBackedInventoryStore:
    """JSON metadata file + per-key file in a sibling ``keys/`` dir.

    Layout::

        {path}                       JSON {"hosts": [...], "keys": [...]}
        {path.parent}/keys/{key_id}  PEM content, chmod 0600

    Each mutation rewrites the JSON file atomically (tmp + rename), so a
    crashed write never leaves partial state. Reads acquire the same
    lock so a concurrent writer can't be observed mid-rewrite.

    Used by both the dashboard pod (path = mounted volume) and the TUI
    (path = ``~/.olympus/inventory.json``).
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.keys_dir = self.path.parent / "keys"
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.keys_dir, 0o700)
        except OSError:
            pass
        self._lock = threading.RLock()

    # ----- IO -----

    def _read(self) -> tuple[dict[str, HostEntry], dict[str, SshKey]]:
        if not self.path.exists():
            return {}, {}
        try:
            raw = json.loads(self.path.read_text("utf-8") or "{}")
        except (json.JSONDecodeError, OSError):
            return {}, {}
        hosts: dict[str, HostEntry] = {}
        keys: dict[str, SshKey] = {}
        for h in raw.get("hosts", []) or []:
            try:
                # Tolerate older snapshots that didn't have updated_at.
                if "updated_at" not in h:
                    h["updated_at"] = h.get("created_at", time.time())
                hosts[h["id"]] = HostEntry(**h)
            except (TypeError, KeyError):
                continue
        for k in raw.get("keys", []) or []:
            try:
                keys[k["id"]] = SshKey(**k)
            except (TypeError, KeyError):
                continue
        return hosts, keys

    def _write(
        self,
        hosts: dict[str, HostEntry],
        keys: dict[str, SshKey],
    ) -> None:
        payload = {
            "hosts": [asdict(h) for h in hosts.values()],
            "keys": [asdict(k) for k in keys.values()],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
        tmp.replace(self.path)

    def _key_path(self, key_id: str) -> Path:
        return self.keys_dir / key_id

    # ----- hosts -----

    def list_hosts(self) -> list[HostEntry]:
        with self._lock:
            hosts, _ = self._read()
        return sorted((HostEntry(**asdict(h)) for h in hosts.values()),
                      key=lambda h: h.name)

    def get_host(self, host_id: str) -> Optional[HostEntry]:
        with self._lock:
            hosts, _ = self._read()
        h = hosts.get(host_id)
        return HostEntry(**asdict(h)) if h else None

    def get_host_by_name(self, name: str) -> Optional[HostEntry]:
        with self._lock:
            hosts, _ = self._read()
        for h in hosts.values():
            if h.name == name:
                return HostEntry(**asdict(h))
        return None

    def add_host(
        self,
        *,
        name: str,
        address: str,
        ssh_user: str = "ubuntu",
        ssh_port: int = 22,
        key_id: Optional[str] = None,
        groups: Optional[list[str]] = None,
        vars: Optional[Mapping[str, Any]] = None,
        description: str = "",
    ) -> HostEntry:
        n = _validate_name(name)
        addr = _validate_address(address)
        port = _validate_port(ssh_port)
        user = _validate_name(ssh_user, field_label="ssh_user")
        clean_groups = _validate_groups(groups)
        clean_vars = _validate_vars(vars)
        with self._lock:
            hosts, keys = self._read()
            if any(h.name == n for h in hosts.values()):
                raise InventoryError(f"host with name '{n}' already exists")
            if key_id is not None and key_id not in keys:
                raise InventoryError(f"key_id '{key_id}' not found")
            now = time.time()
            entry = HostEntry(
                id=_new_id(),
                name=n,
                address=addr,
                ssh_user=user,
                ssh_port=port,
                key_id=key_id or None,
                groups=clean_groups,
                vars=clean_vars,
                description=(description or "").strip(),
                created_at=now,
                updated_at=now,
            )
            hosts[entry.id] = entry
            self._write(hosts, keys)
            return HostEntry(**asdict(entry))

    def update_host(self, host_id: str, **fields: Any) -> HostEntry:
        with self._lock:
            hosts, keys = self._read()
            existing = hosts.get(host_id)
            if existing is None:
                raise InventoryError(f"host '{host_id}' not found")
            current = asdict(existing)
            if "name" in fields:
                current["name"] = _validate_name(fields["name"])
                if any(
                    h.name == current["name"] and h.id != host_id
                    for h in hosts.values()
                ):
                    raise InventoryError(
                        f"host with name '{current['name']}' already exists"
                    )
            if "address" in fields:
                current["address"] = _validate_address(fields["address"])
            if "ssh_user" in fields:
                current["ssh_user"] = _validate_name(
                    fields["ssh_user"], field_label="ssh_user"
                )
            if "ssh_port" in fields:
                current["ssh_port"] = _validate_port(fields["ssh_port"])
            if "key_id" in fields:
                kid = fields["key_id"]
                if kid is not None and kid not in keys:
                    raise InventoryError(f"key_id '{kid}' not found")
                current["key_id"] = kid or None
            if "groups" in fields:
                current["groups"] = _validate_groups(fields["groups"])
            if "vars" in fields:
                current["vars"] = _validate_vars(fields["vars"])
            if "description" in fields:
                current["description"] = (fields["description"] or "").strip()
            current["updated_at"] = time.time()
            current["id"] = existing.id
            current["created_at"] = existing.created_at
            new_entry = HostEntry(**current)
            hosts[host_id] = new_entry
            self._write(hosts, keys)
            return HostEntry(**asdict(new_entry))

    def remove_host(self, host_id: str) -> bool:
        with self._lock:
            hosts, keys = self._read()
            if host_id not in hosts:
                return False
            del hosts[host_id]
            self._write(hosts, keys)
            return True

    # ----- keys -----

    def list_keys(self) -> list[SshKey]:
        with self._lock:
            _, keys = self._read()
        return sorted((SshKey(**asdict(k)) for k in keys.values()),
                      key=lambda k: k.name)

    def get_key(self, key_id: str) -> Optional[SshKey]:
        with self._lock:
            _, keys = self._read()
        k = keys.get(key_id)
        return SshKey(**asdict(k)) if k else None

    def add_key(self, *, name: str, content: str) -> SshKey:
        n = _validate_name(name)
        body = (content or "").strip()
        if not body:
            raise InventoryError("key content is empty")
        if not _looks_like_private_key(body):
            raise InventoryError(
                "content does not look like a PEM private key "
                "(missing -----BEGIN ... PRIVATE KEY----- header)"
            )
        fp = ssh_key_fingerprint(body)
        with self._lock:
            hosts, keys = self._read()
            if any(k.name == n for k in keys.values()):
                raise InventoryError(f"key with name '{n}' already exists")
            entry = SshKey(id=_new_id(), name=n, fingerprint=fp)
            keys[entry.id] = entry
            # Write the key body to disk first, atomically, with 0600.
            kp = self._key_path(entry.id)
            tmp = kp.with_suffix(".tmp")
            tmp.write_text(body + ("\n" if not body.endswith("\n") else ""),
                           "utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(kp)
            try:
                self._write(hosts, keys)
            except Exception:
                # Roll the key file back if metadata write fails — keeps
                # the on-disk state consistent.
                try:
                    kp.unlink()
                except OSError:
                    pass
                raise
            return SshKey(**asdict(entry))

    def get_key_content(self, key_id: str) -> Optional[str]:
        with self._lock:
            _, keys = self._read()
            if key_id not in keys:
                return None
            kp = self._key_path(key_id)
            if not kp.exists():
                return None
            return kp.read_text("utf-8")

    def remove_key(self, key_id: str) -> bool:
        with self._lock:
            hosts, keys = self._read()
            if key_id not in keys:
                return False
            in_use = [h.name for h in hosts.values() if h.key_id == key_id]
            if in_use:
                raise InventoryError(
                    f"key is in use by host(s): {', '.join(in_use)}"
                )
            del keys[key_id]
            self._write(hosts, keys)
            try:
                self._key_path(key_id).unlink()
            except OSError:
                pass
            return True


# ---------------------------------------------------------------------------
# Inventory rendering + run-dir materialization
# ---------------------------------------------------------------------------


def _ini_quote(value: str) -> str:
    """Quote an ansible INI host var value if it contains whitespace
    or any of ``='"#``. Otherwise return as-is. Ansible's INI parser
    treats double quotes as the canonical quoting form for values
    containing spaces."""
    if value == "":
        return '""'
    if any(c.isspace() or c in "='\"#" for c in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _render_host_line(host: HostEntry, key_path: Optional[str]) -> str:
    parts: list[str] = [host.name, f"ansible_host={host.address}"]
    if host.ssh_user:
        parts.append(f"ansible_user={host.ssh_user}")
    if host.ssh_port and host.ssh_port != 22:
        parts.append(f"ansible_port={host.ssh_port}")
    if key_path:
        parts.append(f"ansible_ssh_private_key_file={key_path}")
    for k in sorted(host.vars.keys()):
        parts.append(f"{k}={_ini_quote(str(host.vars[k]))}")
    return " ".join(parts)


def render_ansible_inventory(
    hosts: Iterable[HostEntry],
    *,
    key_paths: Optional[Mapping[str, str]] = None,
) -> str:
    """Render an iterable of HostEntries to ansible INI inventory text.

    Each host appears under every group it declares. Hosts with no
    explicit groups land under ``[ungrouped]`` (ansible's default name
    for that pool). All hosts implicitly belong to ``all`` — ansible
    handles that without us emitting an ``[all]`` section.

    ``key_paths`` maps ``key_id -> filesystem_path``; matching hosts
    get an ``ansible_ssh_private_key_file=<path>`` attr. Hosts with no
    key (or a key not in the map) skip that attr.

    Output is sorted by group name (then by host name within each
    group) so identical inputs render identically — useful for diffing
    in tests and audit logs.
    """
    kp = dict(key_paths or {})
    hosts_list = list(hosts)
    groups: dict[str, list[HostEntry]] = {}
    for h in hosts_list:
        if not h.groups:
            groups.setdefault("ungrouped", []).append(h)
        else:
            for g in h.groups:
                groups.setdefault(g, []).append(h)
    lines: list[str] = [
        "# Generated by Olympus inventory store. Do not hand-edit.",
        "",
    ]
    for group_name in sorted(groups.keys()):
        lines.append(f"[{group_name}]")
        for host in sorted(groups[group_name], key=lambda h: h.name):
            key_path = kp.get(host.key_id) if host.key_id else None
            lines.append(_render_host_line(host, key_path))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class MaterializedInventory:
    """Result of writing a run-dir for an ansible invocation.

    ``inventory_path`` is the rendered ``inventory.ini`` to pass to
    ansible (or set as ``ANSIBLE_INVENTORY``). ``key_paths`` is the map
    of key_id → on-disk path the inventory references. ``run_dir`` is
    the root that should be removed after the run (use a contextmanager
    or ``materialize_run_dir`` inside ``tempfile.TemporaryDirectory``).
    """

    run_dir: Path
    inventory_path: Path
    key_paths: dict[str, str]


def materialize_run_dir(
    store: InventoryStore,
    run_dir: str | os.PathLike[str],
) -> MaterializedInventory:
    """Write ``inventory.ini`` + per-key files into ``run_dir``.

    The ansible agent should call this inside a
    ``tempfile.TemporaryDirectory`` so the keys are cleaned up after
    the run. Keys land at ``{run_dir}/keys/{key_id}`` with 0600 perms.
    Hosts that reference an unknown ``key_id`` still render — they just
    won't get an ``ansible_ssh_private_key_file`` line.
    """
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    keys_dir = root / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(keys_dir, 0o700)
    except OSError:
        pass

    hosts = store.list_hosts()
    referenced: set[str] = {h.key_id for h in hosts if h.key_id}
    key_paths: dict[str, str] = {}
    for kid in referenced:
        content = store.get_key_content(kid)
        if content is None:
            continue
        kp = keys_dir / kid
        kp.write_text(content + ("\n" if not content.endswith("\n") else ""),
                      "utf-8")
        try:
            os.chmod(kp, 0o600)
        except OSError:
            pass
        key_paths[kid] = str(kp)

    inv_text = render_ansible_inventory(hosts, key_paths=key_paths)
    inv_path = root / "inventory.ini"
    inv_path.write_text(inv_text, "utf-8")
    return MaterializedInventory(
        run_dir=root,
        inventory_path=inv_path,
        key_paths=key_paths,
    )


__all__ = [
    "HostEntry",
    "SshKey",
    "InventoryError",
    "InventoryStore",
    "InMemoryInventoryStore",
    "FileBackedInventoryStore",
    "MaterializedInventory",
    "render_ansible_inventory",
    "materialize_run_dir",
    "ssh_key_fingerprint",
]
