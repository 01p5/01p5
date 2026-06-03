import { useEffect, useState } from "react";
import { Network, Plus, Key, Trash2, Eye, EyeOff, DownloadCloud } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../hooks/useAuth";
import type { InventoryHost, InventorySshKey } from "../types";
import { Modal } from "../components/Modal";

/**
 * Hosts — user-managed inventory + SSH keys. The Ansible agent reads
 * from this at run time (rendered to inventory.ini in a per-run dir);
 * the Sysadmin agent's ssh_run tool resolves host aliases against it.
 *
 * Keys are paste-once: the body is sent on POST and never returned by
 * the API. The list shows id + name + fingerprint only.
 */
export function HostsPage(): JSX.Element {
  const [hosts, setHosts] = useState<InventoryHost[]>([]);
  const [keys, setKeys] = useState<InventorySshKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hostModalOpen, setHostModalOpen] = useState(false);
  const [editingHost, setEditingHost] = useState<InventoryHost | null>(null);
  const [keyModalOpen, setKeyModalOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState<string>("");
  // Sync-from-terraform (admin): read a stack's olympus_inventory_hosts output.
  const { auth } = useAuth();
  const isAdmin = auth.state === "authed" && auth.isAdmin;
  const [syncOpen, setSyncOpen] = useState(false);
  const [syncDir, setSyncDir] = useState("/tmp/demo-host");
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const refresh = async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const [h, k] = await Promise.all([api.listHosts(), api.listKeys()]);
      setHosts(h);
      setKeys(k);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const onDeleteHost = async (id: string): Promise<void> => {
    if (!window.confirm("Delete this host?")) return;
    try {
      await api.removeHost(id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onDeleteKey = async (id: string): Promise<void> => {
    if (!window.confirm("Delete this key? Any host using it will block.")) return;
    try {
      await api.removeKey(id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onShowPreview = async (): Promise<void> => {
    try {
      const text = await api.renderInventory();
      setPreview(text);
      setPreviewOpen(true);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onSync = async (): Promise<void> => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const r = await api.syncTerraform(syncDir.trim());
      const parts = [
        r.added.length ? `added ${r.added.join(", ")}` : null,
        r.skipped.length ? `skipped ${r.skipped.join(", ")}` : null,
        r.errors.length ? `errors: ${r.errors.map((e) => `${e.host} (${e.error})`).join("; ")}` : null,
      ].filter(Boolean);
      setSyncMsg(parts.length ? parts.join(" · ") : "no hosts in that output");
      await refresh();
    } catch (e) {
      setSyncMsg(`failed: ${(e as Error).message}`);
    } finally {
      setSyncing(false);
    }
  };

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 pt-6 pb-3 max-w-5xl mx-auto w-full flex items-center gap-3">
        <Network size={16} className="text-accent-blue" strokeWidth={2.25} />
        <h1 className="font-display text-xl font-semibold text-text-primary">Hosts</h1>
        <span className="text-[11px] font-mono text-text-muted">
          ssh inventory · used by ansible + sysadmin agents
        </span>
        <div className="flex-1" />
        {isAdmin && (
          <button
            onClick={() => setSyncOpen((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green hover:text-text-primary border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors"
          >
            <DownloadCloud size={16} strokeWidth={2.25} />
            Sync from Terraform
          </button>
        )}
        <button
          onClick={() => void onShowPreview()}
          className="px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-text-primary border border-border-subtle hover:border-text-secondary/40 rounded transition-colors"
        >
          Preview inventory.ini
        </button>
      </div>

      {isAdmin && syncOpen && (
        <div className="px-6 pb-2 max-w-5xl mx-auto w-full">
          <div className="flex items-center gap-2 bg-dark-panel border border-accent-green/30 rounded-md px-3 py-2">
            <span className="text-[11px] font-mono text-text-muted shrink-0">
              terraform working dir:
            </span>
            <input
              value={syncDir}
              onChange={(e) => setSyncDir(e.target.value)}
              placeholder="/path/to/applied/stack"
              className="flex-1 bg-dark-control border border-border-subtle rounded px-2 py-1 text-[12px] font-mono text-text-primary focus:outline-none focus:border-accent-green/60"
            />
            <button
              onClick={() => void onSync()}
              disabled={syncing || !syncDir.trim()}
              className="px-3 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors disabled:opacity-40"
            >
              {syncing ? "Syncing…" : "Sync"}
            </button>
          </div>
          {syncMsg && (
            <div className="text-[11px] font-mono text-text-secondary mt-1 px-1">{syncMsg}</div>
          )}
          <div className="text-[10px] font-mono text-text-muted mt-1 px-1">
            Reads the stack's <code>olympus_inventory_hosts</code> output and registers each host.
          </div>
        </div>
      )}

      <div className="flex-1 overflow-auto px-6 py-6 max-w-5xl mx-auto w-full space-y-8">
        {loading && <div className="text-sm text-text-muted font-mono">loading…</div>}
        {error && (
          <div className="text-sm text-accent-red font-mono bg-accent-red/10 border border-accent-red/40 rounded px-3 py-2">
            {error}
          </div>
        )}

        {/* Hosts section */}
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <h2 className="font-display text-sm font-semibold text-text-primary uppercase tracking-wide">Hosts</h2>
            <span className="text-[11px] font-mono text-text-muted">{hosts.length}</span>
            <div className="flex-1" />
            <button
              onClick={() => { setEditingHost(null); setHostModalOpen(true); }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-blue hover:text-text-primary border border-accent-blue/40 hover:bg-accent-blue/10 rounded transition-colors"
            >
              <Plus size={18} strokeWidth={2.5} />
              Add host
            </button>
          </div>
          {hosts.length === 0 && !loading && (
            <div className="text-sm text-text-muted italic py-6 text-center border border-dashed border-border-subtle rounded">
              No hosts yet. Add one to give the ansible / sysadmin agents a target.
            </div>
          )}
          {hosts.map((h) => (
            <div
              key={h.id}
              className="bg-dark-panel border border-border-subtle rounded-md px-4 py-3 hover:border-text-secondary/40 transition-colors"
            >
              <div className="flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-3 flex-wrap">
                    <span className="font-mono text-sm font-semibold text-text-primary">{h.name}</span>
                    <span className="font-mono text-[11px] text-text-secondary">
                      {h.ssh_user}@{h.address}{h.ssh_port !== 22 && `:${h.ssh_port}`}
                    </span>
                    {h.groups.length > 0 && (
                      <span className="font-mono text-[10px] text-text-muted">
                        groups: {h.groups.join(", ")}
                      </span>
                    )}
                    {h.key_id && (
                      <span className="font-mono text-[10px] text-accent-green/70">
                        key: {keys.find((k) => k.id === h.key_id)?.name ?? h.key_id.slice(0, 8)}
                      </span>
                    )}
                  </div>
                  {Object.keys(h.vars).length > 0 && (
                    <div className="font-mono text-[10px] text-text-muted mt-1 truncate">
                      vars: {Object.entries(h.vars).map(([k, v]) => `${k}=${v}`).join(", ")}
                    </div>
                  )}
                  {h.description && (
                    <div className="text-[11px] text-text-secondary mt-1">{h.description}</div>
                  )}
                </div>
                <button
                  onClick={() => { setEditingHost(h); setHostModalOpen(true); }}
                  className="text-[10px] uppercase tracking-[1.5px] text-text-muted hover:text-text-primary px-2 py-1"
                >
                  Edit
                </button>
                <button
                  onClick={() => void onDeleteHost(h.id)}
                  className="text-text-muted hover:text-accent-red p-1"
                  aria-label="Delete host"
                >
                  <Trash2 size={16} strokeWidth={2.25} />
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* Keys section */}
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <h2 className="font-display text-sm font-semibold text-text-primary uppercase tracking-wide">SSH keys</h2>
            <span className="text-[11px] font-mono text-text-muted">{keys.length}</span>
            <div className="flex-1" />
            <button
              onClick={() => setKeyModalOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green hover:text-text-primary border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors"
            >
              <Key size={18} strokeWidth={2.5} />
              Add key
            </button>
          </div>
          {keys.length === 0 && !loading && (
            <div className="text-sm text-text-muted italic py-6 text-center border border-dashed border-border-subtle rounded">
              No keys yet. Paste a private key here so agents can ssh into your hosts.
            </div>
          )}
          {keys.map((k) => (
            <div
              key={k.id}
              className="bg-dark-panel border border-border-subtle rounded-md px-4 py-3 flex items-center gap-3"
            >
              <Key size={16} strokeWidth={2.25} className="text-accent-green/80" />
              <div className="flex-1 min-w-0">
                <div className="font-mono text-sm font-semibold text-text-primary">{k.name}</div>
                <div className="font-mono text-[10px] text-text-muted truncate">{k.fingerprint}</div>
              </div>
              <span className="text-[10px] font-mono text-text-muted">
                {new Date(k.created_at * 1000).toLocaleDateString()}
              </span>
              <button
                onClick={() => void onDeleteKey(k.id)}
                className="text-text-muted hover:text-accent-red p-1"
                aria-label="Delete key"
              >
                <Trash2 size={16} strokeWidth={2.25} />
              </button>
            </div>
          ))}
          <p className="text-[10px] font-mono text-text-muted flex items-center gap-1.5">
            <EyeOff size={13} strokeWidth={2.25} />
            Private keys are paste-once. They are stored 0600 on the dashboard pod and never returned by the API.
          </p>
        </div>
      </div>

      <HostModal
        open={hostModalOpen}
        onClose={() => setHostModalOpen(false)}
        editing={editingHost}
        keys={keys}
        onSaved={() => { setHostModalOpen(false); void refresh(); }}
      />

      <KeyModal
        open={keyModalOpen}
        onClose={() => setKeyModalOpen(false)}
        onSaved={() => { setKeyModalOpen(false); void refresh(); }}
      />

      <Modal open={previewOpen} onClose={() => setPreviewOpen(false)}
             title="Rendered inventory.ini" wide>
        <pre className="font-mono text-[12px] text-text-primary whitespace-pre overflow-auto">
          {preview || "(empty)"}
        </pre>
      </Modal>
    </section>
  );
}


function HostModal({
  open, onClose, editing, keys, onSaved,
}: {
  open: boolean;
  onClose: () => void;
  editing: InventoryHost | null;
  keys: InventorySshKey[];
  onSaved: () => void;
}): JSX.Element {
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [sshUser, setSshUser] = useState("ubuntu");
  const [sshPort, setSshPort] = useState(22);
  const [keyId, setKeyId] = useState<string>("");
  const [groups, setGroups] = useState("");
  const [varsText, setVarsText] = useState("");
  const [description, setDescription] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (editing) {
      setName(editing.name);
      setAddress(editing.address);
      setSshUser(editing.ssh_user);
      setSshPort(editing.ssh_port);
      setKeyId(editing.key_id ?? "");
      setGroups(editing.groups.join(", "));
      setVarsText(
        Object.entries(editing.vars).map(([k, v]) => `${k}=${v}`).join("\n"),
      );
      setDescription(editing.description);
    } else {
      setName(""); setAddress(""); setSshUser("ubuntu"); setSshPort(22);
      setKeyId(""); setGroups(""); setVarsText(""); setDescription("");
    }
    setErr(null);
  }, [open, editing]);

  const onSave = async (): Promise<void> => {
    setErr(null);
    setSaving(true);
    try {
      const parsedGroups = groups
        .split(",")
        .map((g) => g.trim())
        .filter(Boolean);
      const parsedVars: Record<string, string> = {};
      for (const line of varsText.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const eq = trimmed.indexOf("=");
        if (eq < 0) throw new Error(`vars line missing '=': ${trimmed}`);
        parsedVars[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
      }
      const body = {
        name, address, ssh_user: sshUser, ssh_port: sshPort,
        key_id: keyId || null,
        groups: parsedGroups,
        vars: parsedVars,
        description,
      };
      if (editing) await api.updateHost(editing.id, body);
      else await api.addHost(body);
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose}
           title={editing ? `Edit host: ${editing.name}` : "Add host"}>
      <div className="space-y-3">
        <Field label="Name (alias)" hint="alphanumeric, dot, underscore, dash">
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Address" hint="hostname or IP">
            <input value={address} onChange={(e) => setAddress(e.target.value)} className={inputCls} />
          </Field>
          <Field label="SSH user">
            <input value={sshUser} onChange={(e) => setSshUser(e.target.value)} className={inputCls} />
          </Field>
          <Field label="SSH port">
            <input type="number" value={sshPort} onChange={(e) => setSshPort(Number(e.target.value || 22))}
                   className={inputCls} />
          </Field>
        </div>
        <Field label="SSH key" hint="optional">
          <select value={keyId} onChange={(e) => setKeyId(e.target.value)} className={inputCls}>
            <option value="">(no key — use ssh-agent / default identity)</option>
            {keys.map((k) => (
              <option key={k.id} value={k.id}>{k.name} ({k.fingerprint.slice(7, 19)}…)</option>
            ))}
          </select>
        </Field>
        <Field label="Groups" hint="comma-separated, e.g. control_plane, monitored">
          <input value={groups} onChange={(e) => setGroups(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Custom ansible vars" hint="one per line, key=value (e.g. region=us-west-2)">
          <textarea value={varsText} onChange={(e) => setVarsText(e.target.value)}
                    rows={4} className={inputCls + " font-mono"} />
        </Field>
        <Field label="Description" hint="optional, just for humans">
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} />
        </Field>
        {err && <div className="text-sm text-accent-red font-mono">{err}</div>}
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose}
                  className="px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-text-primary">
            Cancel
          </button>
          <button onClick={() => void onSave()} disabled={saving}
                  className="px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-blue border border-accent-blue/40 hover:bg-accent-blue/10 rounded disabled:opacity-50">
            {saving ? "Saving…" : (editing ? "Save" : "Create")}
          </button>
        </div>
      </div>
    </Modal>
  );
}


function KeyModal({
  open, onClose, onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}): JSX.Element {
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [showContent, setShowContent] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(""); setContent(""); setShowContent(false); setErr(null);
  }, [open]);

  const onSave = async (): Promise<void> => {
    setErr(null);
    setSaving(true);
    try {
      await api.addKey(name, content);
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Add SSH key">
      <div className="space-y-3">
        <Field label="Name" hint="alphanumeric, dot, underscore, dash">
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Private key (PEM)" hint={
          <span className="inline-flex items-center gap-1">
            <EyeOff size={13} strokeWidth={2.25} /> Paste-once. Never displayed back. Stored 0600 on disk.
          </span>
        }>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={10}
            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----"
            className={inputCls + " font-mono"}
            style={{ WebkitTextSecurity: showContent ? "none" : "disc" } as React.CSSProperties}
          />
        </Field>
        <button
          type="button"
          onClick={() => setShowContent(!showContent)}
          className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted hover:text-text-secondary"
        >
          {showContent ? <EyeOff size={14} strokeWidth={2.25} /> : <Eye size={14} strokeWidth={2.25} />}
          {showContent ? "Hide" : "Show"} content
        </button>
        {err && <div className="text-sm text-accent-red font-mono">{err}</div>}
        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose}
                  className="px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-text-primary">
            Cancel
          </button>
          <button onClick={() => void onSave()} disabled={saving}
                  className="px-3 py-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded disabled:opacity-50">
            {saving ? "Saving…" : "Create"}
          </button>
        </div>
      </div>
    </Modal>
  );
}


const inputCls = "w-full bg-dark-primary border border-border-subtle rounded px-2.5 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent-blue/60";

function Field({ label, hint, children }: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="space-y-1">
      <label className="block text-[10px] font-mono uppercase tracking-[1.5px] text-text-secondary">
        {label}
        {hint && <span className="ml-2 normal-case tracking-normal text-text-muted">— {hint}</span>}
      </label>
      {children}
    </div>
  );
}
