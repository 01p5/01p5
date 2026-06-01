"""
Self-protection policy — stop Olympus from managing the cluster / VM hosts it
is itself deployed onto.

Threat model: a *malicious authenticated user* drives the agents (via chat) to
(a) steal credentials, or (b) escalate their own permissions by tampering with
Olympus's own accounting DB / code / RBAC. Approval-gating does not help — the
malicious user approves their own escalation. So this policy is enforced in the
runtime tool-gate (``runtime.gate_tools``) and **hard-denies** self-targeting
calls *before* the approval hook and *before* the tool executes. There is no
approval card to approve, and no role-based override: self-ops are done
out-of-band (kubectl/ssh from a laptop), never through Olympus.

"Self" identity comes from process **config**, never the user-editable inventory
(a malicious user can rename/re-point inventory aliases):

  - ``OLYMPUS_SELF_NAMESPACE``  — the release namespace (injected via the k8s
    downward API). Joined with the cluster-infra namespaces to form the
    protected-namespace set.
  - ``OLYMPUS_SELF_NODES``      — comma/space-separated IPs **and** hostnames of
    the control-plane + worker nodes Olympus runs on (set at deploy time).

The policy is a no-op when neither is configured (dev / laptop / tests), matching
how every other ``AgentContext`` seam tolerates being absent.

Dependency-free on purpose (no yaml dep — manifests are string-scanned, the same
approach ``sysadmin.tools._scrub_server_fields`` already uses).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Optional

# Cluster-infra namespaces that are never a legitimate target for the
# self-deployed instance, independent of which namespace Olympus runs in.
_CLUSTER_INFRA_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease"})

# A loopback target is the pod itself — always self.
_LOCALHOST_PATTERNS = frozenset({"localhost", "127.0.0.1", "::1"})

# RBAC + cluster-privileged kinds. Applying any of these is a self-escalation
# vector (grant yourself a ClusterRoleBinding, swap a ServiceAccount, register a
# mutating webhook), so apply_manifest of these kinds is hard-denied outright.
_PRIVILEGED_KINDS = frozenset({
    "clusterrole", "clusterrolebinding", "role", "rolebinding",
    "serviceaccount", "mutatingwebhookconfiguration",
    "validatingwebhookconfiguration",
})

# Tools that are arbitrary in-pod execution: they can read the pod's own mounted
# secrets / host SSH key regardless of arguments, so a per-target check can't
# sandbox them. Hard-denied whenever the policy is enabled (equivalent to
# "not available on this deployment").
_ALWAYS_DENY_TOOLS = frozenset({"shell_exec"})

# Ansible host-pattern separators (``web:db``, ``web,db``, ``all:!staging``).
_ANSIBLE_PATTERN_SPLIT = re.compile(r"[\s:,&]+")


def _normalize_node(value: str) -> str:
    """Lowercase + strip a host/IP, dropping an optional ``:port`` and IPv6
    brackets so ``10.0.3.20``, ``10.0.3.20:22`` and ``[::1]:22`` compare
    consistently against the configured node set."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if v.startswith("[") and "]" in v:  # [ipv6](:port)
        return v[1:v.index("]")]
    if v.count(":") == 1:  # host:port (a bare IPv6 has >1 colon — leave it)
        return v.split(":", 1)[0]
    return v


@dataclass(frozen=True)
class SelfProtectionPolicy:
    """Decides whether a tool call targets the Olympus deployment itself.

    Construct via :meth:`from_env`. ``check`` returns a human/LLM-readable deny
    reason when a call self-targets, else ``None``."""

    self_namespace: Optional[str]
    protected_namespaces: frozenset
    self_nodes: frozenset
    enabled: bool

    # ------------------------------------------------------------------ build

    @classmethod
    def from_env(cls, environ: Optional[dict] = None) -> "SelfProtectionPolicy":
        env = environ if environ is not None else os.environ
        ns = (env.get("OLYMPUS_SELF_NAMESPACE") or "").strip() or None
        nodes = frozenset(
            n for n in (
                _normalize_node(x)
                for x in re.split(r"[\s,]+", env.get("OLYMPUS_SELF_NODES") or "")
            ) if n
        )
        protected = frozenset(
            {x.lower() for x in _CLUSTER_INFRA_NAMESPACES}
            | ({ns.lower()} if ns else set())
        )
        # Enabled the moment we know *anything* about self. In-cluster the
        # downward-API namespace guarantees this; dev runs leave it off.
        enabled = bool(ns or nodes)
        return cls(
            self_namespace=ns,
            protected_namespaces=protected,
            self_nodes=nodes,
            enabled=enabled,
        )

    # ------------------------------------------------------------------ check

    def check(
        self,
        tool_name: str,
        args: dict,
        *,
        inventory_store: Any = None,
    ) -> Optional[str]:
        """Return a deny reason if ``tool_name(**args)`` self-targets, else None."""
        if not self.enabled:
            return None
        args = args or {}

        if tool_name in _ALWAYS_DENY_TOOLS:
            return (
                f"DENIED by self-protection: {tool_name} (arbitrary in-pod "
                "execution) is disabled on this deployment — it could read the "
                "pod's own credentials and host SSH key. This cannot be "
                "approved; run such commands out-of-band instead."
            )
        if tool_name == "delete_pod":
            return self._deny_namespace(args.get("namespace"), "delete pods in")
        if tool_name == "apply_manifest":
            return self._check_manifest(args)
        if tool_name == "ssh_run":
            return self._check_ssh(args, inventory_store)
        if tool_name in ("run_playbook", "run_module"):
            return self._check_ansible(tool_name, args, inventory_store)
        return None

    # --------------------------------------------------------------- helpers

    def _is_self_node(self, value: str) -> bool:
        v = _normalize_node(value)
        return bool(v) and (v in self.self_nodes or v in _LOCALHOST_PATTERNS)

    def _deny_namespace(self, ns: Any, verb: str) -> Optional[str]:
        # kubectl's default namespace is "default" when none is given.
        n = (str(ns).strip().lower() if ns else "") or "default"
        if n in self.protected_namespaces:
            return (
                f"DENIED by self-protection: cannot {verb} the protected "
                f"namespace {n!r} (Olympus's own / cluster-infra namespace). "
                "This cannot be approved."
            )
        return None

    def _check_manifest(self, args: dict) -> Optional[str]:
        # The kubectl -n arg first (the tool always passes one).
        deny = self._deny_namespace(args.get("namespace"), "apply manifests into")
        if deny:
            return deny
        yaml = args.get("yaml") or ""
        if not isinstance(yaml, str):
            return None
        for m in re.finditer(r"(?im)^\s*kind:\s*[\"']?([A-Za-z0-9]+)", yaml):
            if m.group(1).strip().lower() in _PRIVILEGED_KINDS:
                return (
                    f"DENIED by self-protection: cannot apply a privileged "
                    f"resource kind {m.group(1)!r} (RBAC / cluster-scoped). "
                    "This is a self-escalation vector and cannot be approved."
                )
        for m in re.finditer(r"(?im)^\s*namespace:\s*[\"']?([A-Za-z0-9._-]+)", yaml):
            if m.group(1).strip().lower() in self.protected_namespaces:
                return (
                    f"DENIED by self-protection: manifest targets the protected "
                    f"namespace {m.group(1)!r}. This cannot be approved."
                )
        return None

    def _check_ssh(self, args: dict, store: Any) -> Optional[str]:
        alias = (args.get("host_alias") or "").strip()
        if not alias:
            return None
        if self._is_self_node(alias):
            return self._ssh_deny(alias, alias)
        if store is not None:
            try:
                host = store.get_host_by_name(alias)
            except Exception:
                host = None
            if host is not None and self._is_self_node(getattr(host, "address", "")):
                return self._ssh_deny(alias, host.address)
        return None

    @staticmethod
    def _ssh_deny(alias: str, address: str) -> str:
        return (
            f"DENIED by self-protection: ssh_run targets host {alias!r} "
            f"({address}), an Olympus self-node. Managing the cluster/VM hosts "
            "Olympus runs on is hard-blocked and cannot be approved."
        )

    def _self_inventory(self, store: Any) -> tuple:
        """Return (host names, group names) in the inventory that resolve to a
        self-node, so an ansible pattern naming either is denied."""
        names: set = set()
        groups: set = set()
        if store is None:
            return names, groups
        try:
            hosts = store.list_hosts()
        except Exception:
            return names, groups
        for h in hosts:
            if self._is_self_node(getattr(h, "address", "")) or self._is_self_node(
                getattr(h, "name", "")
            ):
                names.add(h.name)
                for g in (getattr(h, "groups", None) or []):
                    groups.add(g)
        return names, groups

    def _check_ansible(self, tool_name: str, args: dict, store: Any) -> Optional[str]:
        key = "limit" if tool_name == "run_playbook" else "pattern"
        pat = (args.get(key) or "").strip()
        # An empty playbook limit means "whatever the play targets". We can't
        # statically resolve that, but the ansible agent excludes self-nodes
        # from the materialized inventory, so they're unreachable regardless.
        if not pat:
            return None
        self_names, self_groups = self._self_inventory(store)
        lower_names = {n.lower() for n in self_names}
        for raw in _ANSIBLE_PATTERN_SPLIT.split(pat):
            tok = raw.strip().lstrip("!")  # !host = exclusion; the bare name still leaks scope
            if not tok:
                continue
            low = tok.lower()
            if low in ("all", "*") or low in _LOCALHOST_PATTERNS:
                return (
                    f"DENIED by self-protection: ansible pattern {pat!r} "
                    f"({tok!r}) would target the hosts Olympus runs on. "
                    "Scope the run to specific non-self hosts instead."
                )
            if low in lower_names or tok in self_groups:
                return (
                    f"DENIED by self-protection: ansible pattern {pat!r} "
                    f"resolves to an Olympus self-node via {tok!r}. "
                    "This is hard-blocked and cannot be approved."
                )
        return None


__all__ = ["SelfProtectionPolicy"]
