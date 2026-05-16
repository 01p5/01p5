import { useEffect, useState } from "react";
import { ListChecks, Eye, Play, AlertTriangle, FileText, Network } from "lucide-react";
import { api } from "../api";
import { Card, SectionHeader } from "../components/Card";
import { Button } from "../components/Button";
import { Modal } from "../components/Modal";
import { CodeBlock } from "../components/CodeBlock";
import { Badge } from "../components/Badge";

const DEFAULT_INVENTORY = "/opt/olympus/infra/terraform/deployment/inventory.ini";

/**
 * Ansible — playbook list. Each card lets you check (dry-run) or run
 * a playbook. Inventory path is a free-text field (defaults to the
 * inventory.ini that the terraform pve module writes out). Output
 * lands in a modal; running goes through the approval queue.
 */
export function AnsiblePage(): JSX.Element {
  const [playbooks, setPlaybooks] = useState<string[] | null>(null);
  const [inventory, setInventory] = useState(DEFAULT_INVENTORY);
  const [limit, setLimit] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [modal, setModal] = useState<{ title: string; text: string } | null>(null);

  useEffect(() => {
    api.ansiblePlaybooks().then(setPlaybooks).catch((e) => setError((e as Error).message));
  }, []);

  const playbookPath = (p: string): string => `/opt/olympus/infra/${p}`;

  const run = async (
    playbook: string, tool: "check_playbook" | "run_playbook", title: string,
  ): Promise<void> => {
    if (tool === "run_playbook" && !window.confirm(
      `Run playbook ${playbook} against ${inventory}?\n\n` +
      `This will make real changes on every matching host. An approval ` +
      `card will surface in the right sidebar.`,
    )) return;
    setBusyKey(`${playbook}:${tool}`);
    try {
      const args: Record<string, unknown> = {
        playbook: playbookPath(playbook),
        inventory,
      };
      if (limit.trim()) args.limit = limit.trim();
      const r = await api.invokeTool("ansible", tool, args);
      setModal({ title: `${title} — ${playbook}`, text: String(r.result ?? "(empty)") });
    } catch (e) {
      setModal({ title: `${title} failed — ${playbook}`, text: (e as Error).message });
    } finally { setBusyKey(null); }
  };

  const listInv = async (): Promise<void> => {
    setBusyKey("listInv");
    try {
      const r = await api.invokeTool("ansible", "list_inventory", { inventory });
      setModal({ title: `Inventory — ${inventory}`, text: String(r.result ?? "(empty)") });
    } catch (e) { setModal({ title: "Inventory error", text: (e as Error).message }); }
    finally { setBusyKey(null); }
  };

  return (
    <section className="flex flex-col min-h-0 h-full">
      <div className="px-6 py-3 border-b border-border-subtle bg-dark-secondary/40 flex items-baseline gap-3">
        <ListChecks size={14} className="text-accent-blue self-center" />
        <h1 className="font-display text-base font-semibold text-text-primary">Ansible</h1>
        <span className="text-[11px] font-mono text-text-muted">
          {playbooks?.length ?? "…"} playbook{playbooks?.length === 1 ? "" : "s"} discovered
        </span>
      </div>

      <div className="flex-1 overflow-auto px-6 py-5 space-y-5">
        {error && (
          <Card className="p-4 text-accent-red font-mono text-sm">
            <AlertTriangle size={14} className="inline mr-2" />
            {error}
          </Card>
        )}

        <Card className="p-4 space-y-3">
          <SectionHeader title="Inventory & target" />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field
              label="inventory"
              value={inventory}
              onChange={setInventory}
              placeholder="/path/to/inventory.ini"
            />
            <Field
              label="limit (optional)"
              value={limit}
              onChange={setLimit}
              placeholder="all / master / workers / hostname"
            />
          </div>
          <div>
            <Button
              size="sm"
              icon={<Network size={12} />}
              loading={busyKey === "listInv"}
              onClick={listInv}
            >
              list inventory
            </Button>
          </div>
        </Card>

        {playbooks && playbooks.length > 0 && (
          <>
            <SectionHeader title="Playbooks" hint="from infra/ansible/ in the container" />
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {playbooks.map((p) => (
                <Card key={p} className="p-4 space-y-3">
                  <div className="flex items-baseline justify-between gap-2">
                    <h3 className="font-mono text-sm text-text-primary truncate">
                      {p.replace(/^ansible\//, "")}
                    </h3>
                    <Badge tone="blue">playbook</Badge>
                  </div>
                  <div className="text-[11px] text-text-muted font-mono break-all">
                    /opt/olympus/infra/{p}
                  </div>
                  <div className="grid grid-cols-2 gap-1.5">
                    <Button
                      size="sm"
                      icon={<Eye size={12} />}
                      loading={busyKey === `${p}:check_playbook`}
                      onClick={() => run(p, "check_playbook", "Check")}
                    >
                      check
                    </Button>
                    <Button
                      size="sm"
                      variant="primary"
                      icon={<Play size={12} />}
                      loading={busyKey === `${p}:run_playbook`}
                      onClick={() => run(p, "run_playbook", "Run")}
                    >
                      run
                    </Button>
                  </div>
                </Card>
              ))}
            </div>
          </>
        )}
      </div>

      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={modal?.title ?? ""}
        wide
      >
        {modal && (
          <CodeBlock text={modal.text} language="ansible" maxHeight="65vh" />
        )}
        {modal && (
          <div className="flex items-center gap-2 mt-3 text-xs text-text-muted">
            <FileText size={12} />
            ansible output. Failed hosts: search the output for "FAILED".
          </div>
        )}
      </Modal>
    </section>
  );
}

function Field({
  label, value, onChange, placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}): JSX.Element {
  return (
    <label className="block">
      <div className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-muted mb-1">
        {label}
      </div>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-dark-primary border border-border-subtle rounded px-2 py-1.5 text-sm font-mono text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60"
      />
    </label>
  );
}
