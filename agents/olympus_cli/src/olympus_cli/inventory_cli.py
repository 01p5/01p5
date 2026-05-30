"""
``olympus-inventory`` — terminal CLI for managing the inventory store.

Lives next to the webui's Hosts tab: both surfaces edit the same
``InventoryStore`` shape (HostEntry + SshKey). This CLI uses a
``FileBackedInventoryStore`` at a configurable path (default
``~/.olympus/inventory.json``) so users can manage their own local
inventory without a running dashboard. It's also what the sandbox
ansible bootstrap shells out to (INV.6).

Subcommands::

    olympus-inventory list-hosts [--store PATH] [--json]
    olympus-inventory add-host   --name NAME --address HOST [--ssh-user U]
                                 [--ssh-port P] [--key KEY_NAME]
                                 [--group G ...] [--var K=V ...]
                                 [--description TEXT] [--store PATH]
    olympus-inventory update-host (--id ID | --name NAME) [field=value ...]
    olympus-inventory remove-host (--id ID | --name NAME) [--store PATH]
    olympus-inventory list-keys  [--store PATH] [--json]
    olympus-inventory add-key    --name NAME --file PATH [--store PATH]
    olympus-inventory remove-key (--id ID | --name NAME) [--store PATH]
    olympus-inventory render     [--store PATH]

Exit codes
----------
- 0 on success
- 1 on user-facing validation / not-found errors
- 2 on unexpected runtime errors
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from agentlib import (
    FileBackedInventoryStore,
    HostEntry,
    InventoryError,
    InventoryStore,
    SshKey,
    materialize_run_dir,
    render_ansible_inventory,
)


DEFAULT_STORE = "~/.olympus/inventory.json"


def _open_store(store_path: str) -> InventoryStore:
    return FileBackedInventoryStore(os.path.expanduser(store_path))


def _resolve_host(store: InventoryStore, *, host_id: Optional[str], name: Optional[str]) -> HostEntry:
    if not host_id and not name:
        raise InventoryError("provide --id or --name")
    if host_id:
        h = store.get_host(host_id)
        if h is None:
            raise InventoryError(f"host '{host_id}' not found")
        return h
    h = store.get_host_by_name(name or "")
    if h is None:
        raise InventoryError(f"host named '{name}' not found")
    return h


def _resolve_key(store: InventoryStore, *, key_id: Optional[str], name: Optional[str]) -> SshKey:
    if not key_id and not name:
        raise InventoryError("provide --id or --name")
    if key_id:
        k = store.get_key(key_id)
        if k is None:
            raise InventoryError(f"key '{key_id}' not found")
        return k
    for k in store.list_keys():
        if k.name == name:
            return k
    raise InventoryError(f"key named '{name}' not found")


def _parse_kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise InventoryError(f"--var expects key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_list_hosts(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    hosts = store.list_hosts()
    if args.json:
        # Plain dicts so jq pipelines work without dataclass-aware decoders.
        out = [
            {
                "id": h.id, "name": h.name, "address": h.address,
                "ssh_user": h.ssh_user, "ssh_port": h.ssh_port,
                "key_id": h.key_id, "groups": h.groups, "vars": h.vars,
                "description": h.description,
            }
            for h in hosts
        ]
        print(json.dumps(out, indent=2))
        return 0
    if not hosts:
        print("(no hosts)")
        return 0
    for h in hosts:
        key_note = f" key={h.key_id[:8]}…" if h.key_id else ""
        port_note = f":{h.ssh_port}" if h.ssh_port != 22 else ""
        groups = f" [{','.join(h.groups)}]" if h.groups else ""
        print(f"{h.name:20s}  {h.ssh_user}@{h.address}{port_note}{groups}{key_note}")
        if h.vars:
            for k, v in sorted(h.vars.items()):
                print(f"  {k}={v}")
        if h.description:
            print(f"  # {h.description}")
    return 0


def _cmd_add_host(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    key_id: Optional[str] = None
    if args.key:
        key = _resolve_key(store, key_id=None, name=args.key)
        key_id = key.id
    host = store.add_host(
        name=args.name,
        address=args.address,
        ssh_user=args.ssh_user or "ubuntu",
        ssh_port=args.ssh_port or 22,
        key_id=key_id,
        groups=list(args.group or []),
        vars=_parse_kv(args.var or []),
        description=args.description or "",
    )
    print(f"added host {host.name} ({host.id})")
    return 0


def _cmd_update_host(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    host = _resolve_host(store, host_id=args.id, name=args.name)
    updates: dict[str, object] = {}
    if args.new_name is not None:
        updates["name"] = args.new_name
    if args.address is not None:
        updates["address"] = args.address
    if args.ssh_user is not None:
        updates["ssh_user"] = args.ssh_user
    if args.ssh_port is not None:
        updates["ssh_port"] = args.ssh_port
    if args.key is not None:
        # --key "" clears, --key NAME resolves to that key.
        if args.key == "":
            updates["key_id"] = None
        else:
            updates["key_id"] = _resolve_key(store, key_id=None, name=args.key).id
    if args.group:
        updates["groups"] = list(args.group)
    if args.var:
        updates["vars"] = _parse_kv(args.var)
    if args.description is not None:
        updates["description"] = args.description
    if not updates:
        print("(no changes)")
        return 0
    updated = store.update_host(host.id, **updates)
    print(f"updated host {updated.name} ({updated.id})")
    return 0


def _cmd_remove_host(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    host = _resolve_host(store, host_id=args.id, name=args.name)
    store.remove_host(host.id)
    print(f"removed host {host.name}")
    return 0


def _cmd_list_keys(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    keys = store.list_keys()
    if args.json:
        print(json.dumps(
            [{"id": k.id, "name": k.name, "fingerprint": k.fingerprint,
              "created_at": k.created_at} for k in keys], indent=2,
        ))
        return 0
    if not keys:
        print("(no keys)")
        return 0
    for k in keys:
        print(f"{k.name:20s}  {k.fingerprint}  id={k.id}")
    return 0


def _cmd_add_key(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    path = Path(os.path.expanduser(args.file))
    if not path.is_file():
        raise InventoryError(f"key file '{path}' not found")
    content = path.read_text("utf-8")
    key = store.add_key(name=args.name, content=content)
    print(f"added key {key.name} ({key.id}) {key.fingerprint}")
    return 0


def _cmd_remove_key(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    key = _resolve_key(store, key_id=args.id, name=args.name)
    store.remove_key(key.id)
    print(f"removed key {key.name}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    if args.materialize:
        # Write a real run dir (inventory + keys with 0600) and print its path.
        target = Path(os.path.expanduser(args.materialize))
        target.mkdir(parents=True, exist_ok=True)
        result = materialize_run_dir(store, target)
        print(str(result.inventory_path))
        return 0
    text = render_ansible_inventory(store.list_hosts())
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="olympus-inventory",
        description="Manage Olympus hosts + ssh keys for the ansible/sysadmin agents.",
    )
    p.add_argument("--store", default=DEFAULT_STORE,
                   help=f"path to the inventory.json (default: {DEFAULT_STORE})")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list-hosts", help="List all configured hosts.")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=_cmd_list_hosts)

    pa = sub.add_parser("add-host", help="Add a new host.")
    pa.add_argument("--name", required=True)
    pa.add_argument("--address", required=True)
    pa.add_argument("--ssh-user", default="ubuntu")
    pa.add_argument("--ssh-port", type=int, default=22)
    pa.add_argument("--key", default=None,
                    help="Name of an already-added SSH key (see list-keys).")
    pa.add_argument("--group", action="append", default=[])
    pa.add_argument("--var", action="append", default=[],
                    help="Custom ansible host var, key=value. Repeatable.")
    pa.add_argument("--description", default="")
    pa.set_defaults(func=_cmd_add_host)

    pu = sub.add_parser("update-host", help="Patch an existing host.")
    sel = pu.add_mutually_exclusive_group(required=True)
    sel.add_argument("--id", dest="id")
    sel.add_argument("--name", dest="name")
    pu.add_argument("--new-name", default=None)
    pu.add_argument("--address", default=None)
    pu.add_argument("--ssh-user", default=None)
    pu.add_argument("--ssh-port", type=int, default=None)
    pu.add_argument("--key", default=None,
                    help='Key name to assign, or empty string "" to clear.')
    pu.add_argument("--group", action="append", default=None)
    pu.add_argument("--var", action="append", default=None,
                    help="Replace all vars. Repeatable key=value.")
    pu.add_argument("--description", default=None)
    pu.set_defaults(func=_cmd_update_host)

    pr = sub.add_parser("remove-host", help="Delete a host.")
    selr = pr.add_mutually_exclusive_group(required=True)
    selr.add_argument("--id", dest="id")
    selr.add_argument("--name", dest="name")
    pr.set_defaults(func=_cmd_remove_host)

    plk = sub.add_parser("list-keys", help="List stored SSH keys (fingerprint only).")
    plk.add_argument("--json", action="store_true")
    plk.set_defaults(func=_cmd_list_keys)

    pak = sub.add_parser("add-key", help="Store an SSH private key from a file.")
    pak.add_argument("--name", required=True)
    pak.add_argument("--file", required=True, help="Path to the private key PEM file.")
    pak.set_defaults(func=_cmd_add_key)

    prk = sub.add_parser("remove-key", help="Delete a stored SSH key.")
    selrk = prk.add_mutually_exclusive_group(required=True)
    selrk.add_argument("--id", dest="id")
    selrk.add_argument("--name", dest="name")
    prk.set_defaults(func=_cmd_remove_key)

    prn = sub.add_parser("render",
                         help="Print the ansible INI inventory (or materialize a run dir).")
    prn.add_argument("--materialize", default=None,
                     help="Write inventory + keys into this dir, print the inventory path.")
    prn.set_defaults(func=_cmd_render)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except InventoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover — bug catcher
        print(f"unexpected: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
