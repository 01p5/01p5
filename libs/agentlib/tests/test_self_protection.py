"""Unit tests for SelfProtectionPolicy — the per-tool deny/allow matrix and the
config parsing. The runtime-integration (malicious-approval-bypass) test lives in
test_runtime.py."""
from __future__ import annotations

from agentlib import InMemoryInventoryStore, SelfProtectionPolicy


# --------------------------------------------------------------------------- env

def _policy(ns="olympus", nodes="10.0.3.20, 10.0.3.21, master, worker1"):
    env = {}
    if ns is not None:
        env["OLYMPUS_SELF_NAMESPACE"] = ns
    if nodes is not None:
        env["OLYMPUS_SELF_NODES"] = nodes
    return SelfProtectionPolicy.from_env(env)


def test_from_env_disabled_when_nothing_configured():
    p = SelfProtectionPolicy.from_env({})
    assert p.enabled is False
    # A disabled policy never denies anything.
    assert p.check("delete_pod", {"namespace": "kube-system"}) is None
    assert p.check("shell_exec", {"command": "cat /etc/secret"}) is None


def test_from_env_namespace_only_enables_namespace_protection():
    p = _policy(ns="olympus", nodes=None)
    assert p.enabled is True
    assert "olympus" in p.protected_namespaces
    assert "kube-system" in p.protected_namespaces
    assert p.self_nodes == frozenset()


def test_from_env_nodes_only_enables_and_normalizes():
    p = _policy(ns=None, nodes="10.0.3.20:22,  Master , [::1]:22")
    assert p.enabled is True
    # lowercased, port-stripped, ipv6 de-bracketed
    assert "10.0.3.20" in p.self_nodes
    assert "master" in p.self_nodes
    assert "::1" in p.self_nodes


# ----------------------------------------------------------------- delete_pod

def test_delete_pod_denies_self_and_infra_namespaces():
    p = _policy()
    assert p.check("delete_pod", {"name": "x", "namespace": "olympus"})
    assert p.check("delete_pod", {"name": "x", "namespace": "kube-system"})
    assert p.check("delete_pod", {"name": "x", "namespace": "KUBE-PUBLIC"})
    # default kubectl namespace (none given) is "default" — allowed.
    assert p.check("delete_pod", {"name": "x"}) is None
    assert p.check("delete_pod", {"name": "x", "namespace": "team-a"}) is None


# --------------------------------------------------------------- apply_manifest

def test_apply_manifest_denies_protected_ns_arg():
    p = _policy()
    assert p.check("apply_manifest", {"yaml": "kind: Pod", "namespace": "olympus"})


def test_apply_manifest_denies_privileged_kinds_even_in_benign_ns():
    p = _policy()
    for kind in ("ClusterRoleBinding", "Role", "ServiceAccount",
                 "ValidatingWebhookConfiguration"):
        y = f"apiVersion: v1\nkind: {kind}\nmetadata:\n  name: pwn\n"
        assert p.check("apply_manifest", {"yaml": y, "namespace": "default"}), kind


def test_apply_manifest_denies_protected_ns_in_manifest_body():
    p = _policy()
    y = "kind: ConfigMap\nmetadata:\n  name: c\n  namespace: kube-system\n"
    assert p.check("apply_manifest", {"yaml": y, "namespace": "default"})


def test_apply_manifest_allows_benign_pod_in_user_ns():
    p = _policy()
    y = "apiVersion: v1\nkind: Pod\nmetadata:\n  name: web\n  namespace: team-a\n"
    assert p.check("apply_manifest", {"yaml": y, "namespace": "team-a"}) is None


# -------------------------------------------------------------------- ssh_run

def _store_with_self_host():
    store = InMemoryInventoryStore()
    store.add_host(name="cp", address="10.0.3.20", groups=["controlplane"])
    store.add_host(name="edge", address="203.0.113.9", groups=["edge"])
    return store


def test_ssh_run_denies_alias_resolving_to_self_node():
    p = _policy()
    store = _store_with_self_host()
    assert p.check("ssh_run", {"host_alias": "cp", "command": "id"},
                   inventory_store=store)
    # Renaming the alias doesn't help: a NEW alias pointing at the self IP.
    store.add_host(name="sneaky", address="10.0.3.20")
    assert p.check("ssh_run", {"host_alias": "sneaky", "command": "id"},
                   inventory_store=store)


def test_ssh_run_denies_raw_self_ip_literal():
    p = _policy()
    # Even with no store entry, a literal self-node string is denied.
    assert p.check("ssh_run", {"host_alias": "10.0.3.21", "command": "id"})


def test_ssh_run_allows_non_self_host():
    p = _policy()
    store = _store_with_self_host()
    assert p.check("ssh_run", {"host_alias": "edge", "command": "id"},
                   inventory_store=store) is None


# ------------------------------------------------------------------- ansible

def test_run_module_denies_all_and_localhost_patterns():
    p = _policy()
    assert p.check("run_module", {"inventory": "i", "pattern": "all", "module": "ping"})
    assert p.check("run_module", {"inventory": "i", "pattern": "*", "module": "ping"})
    assert p.check("run_module", {"inventory": "i", "pattern": "localhost", "module": "command"})


def test_run_playbook_denies_limit_resolving_to_self_group_or_host():
    p = _policy()
    store = _store_with_self_host()
    # by host name
    assert p.check("run_playbook", {"playbook": "p", "inventory": "i", "limit": "cp"},
                   inventory_store=store)
    # by group containing a self host
    assert p.check("run_playbook", {"playbook": "p", "inventory": "i", "limit": "controlplane"},
                   inventory_store=store)


def test_run_playbook_allows_non_self_limit_and_empty_limit():
    p = _policy()
    store = _store_with_self_host()
    assert p.check("run_playbook", {"playbook": "p", "inventory": "i", "limit": "edge"},
                   inventory_store=store) is None
    # Empty limit is handled by the inventory exclusion, not a static deny.
    assert p.check("run_playbook", {"playbook": "p", "inventory": "i"},
                   inventory_store=store) is None


# ----------------------------------------------------------------- shell_exec

def test_shell_exec_always_denied_when_enabled():
    p = _policy()
    assert p.check("shell_exec", {"command": "cat /etc/olympus/ssh/k8s.pem"})
    # ...regardless of how benign the command looks.
    assert p.check("shell_exec", {"command": "echo hi"})
