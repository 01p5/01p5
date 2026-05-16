import { useEffect, useState } from "react";
import { RefreshCw, Trash2, FileText, Eye, Server, Layers, Bell } from "lucide-react";
import { api } from "../api";
import { Table, type Column } from "../components/Table";
import { Badge, k8sStatusTone } from "../components/Badge";
import { Button } from "../components/Button";
import { Modal } from "../components/Modal";
import { CodeBlock } from "../components/CodeBlock";

type Tab = "pods" | "nodes" | "events";

interface Pod {
  name: string;
  ready: string;
  status: string;
  restarts: string;
  age: string;
  ip: string;
  node: string;
}
interface NodeRow {
  name: string;
  status: string;
  roles: string;
  age: string;
  version: string;
}
interface EventRow {
  last_seen: string;
  type: string;
  reason: string;
  object: string;
  message: string;
}

/**
 * Kubernetes page — pod / node / event tables with inline action
 * buttons. Each action invokes a sysadmin tool through /tools/sysadmin/*
 * so the audit trail + approval gating still apply. `kubectl`-style
 * column output is parsed client-side from the raw stdout the tool
 * returns; if parsing fails we fall back to a raw view.
 */
export function KubernetesPage(): JSX.Element {
  const [tab, setTab] = useState<Tab>("pods");
  const [namespace, setNamespace] = useState("default");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [tick, setTick] = useState(0);

  // Modals: log viewer / describe / delete confirm
  const [modal, setModal] = useState<null | {
    kind: "logs" | "describe";
    podName: string;
    text: string;
  }>(null);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => setTick((t) => t + 1), 10000);
    return () => clearInterval(id);
  }, [autoRefresh]);

  return (
    <section className="flex flex-col min-h-0 h-full">
      {/* Page header */}
      <div className="px-6 py-3 border-b border-border-subtle bg-dark-secondary/40 flex items-baseline gap-4">
        <div className="flex items-baseline gap-3">
          <Server size={14} className="text-accent-blue self-center" />
          <h1 className="font-display text-base font-semibold text-text-primary">
            Kubernetes
          </h1>
        </div>
        <div className="flex items-baseline gap-3 ml-auto">
          <label className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-muted">
            namespace
          </label>
          <input
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
            className="bg-dark-panel border border-border-subtle rounded px-2 py-1 text-sm font-mono w-32 focus:outline-none focus:border-accent-blue/60"
          />
          <Button
            variant="ghost"
            size="sm"
            icon={<RefreshCw size={12} />}
            onClick={() => setTick((t) => t + 1)}
          >
            refresh
          </Button>
          <label className="text-[11px] font-mono text-text-muted flex items-center gap-1 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="accent-accent-green"
            />
            auto
          </label>
        </div>
      </div>

      {/* Tab strip */}
      <div className="flex gap-1 px-6 py-2 border-b border-border-subtle bg-dark-secondary/20">
        {([["pods", "Pods", Layers], ["nodes", "Nodes", Server], ["events", "Events", Bell]] as const).map(
          ([key, label, Icon]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-2 px-3 py-1.5 text-sm rounded transition-colors ${
                tab === key
                  ? "bg-dark-panel text-text-primary border border-border-subtle"
                  : "text-text-secondary hover:text-text-primary"
              }`}
            >
              <Icon size={13} />
              {label}
            </button>
          ),
        )}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-auto px-6 py-5">
        {tab === "pods" && (
          <PodsTable
            key={`pods-${namespace}-${tick}`}
            namespace={namespace}
            onLogs={(name, text) => setModal({ kind: "logs", podName: name, text })}
            onDescribe={(name, text) => setModal({ kind: "describe", podName: name, text })}
            onRefresh={() => setTick((t) => t + 1)}
          />
        )}
        {tab === "nodes" && <NodesTable key={`nodes-${tick}`} />}
        {tab === "events" && (
          <EventsTable key={`events-${namespace}-${tick}`} namespace={namespace} />
        )}
      </div>

      <Modal
        open={!!modal}
        onClose={() => setModal(null)}
        title={modal ? `${modal.kind === "logs" ? "Logs" : "Describe"} — ${modal.podName}` : ""}
        wide
      >
        {modal && <CodeBlock text={modal.text} maxHeight="70vh" />}
      </Modal>
    </section>
  );
}

// ----- pods -----

function parseKubectlTable(raw: string): Record<string, string>[] {
  // Parse a `kubectl get -o wide` table by header column positions.
  const lines = raw.split("\n").filter((l) => l.trim().length > 0);
  if (lines.length === 0) return [];
  const header = lines[0];
  const colSpecs: Array<{ name: string; start: number; end: number }> = [];
  // Find header tokens + positions.
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  const matches: Array<{ name: string; start: number }> = [];
  while ((m = re.exec(header)) !== null) {
    matches.push({ name: m[0], start: m.index });
  }
  for (let i = 0; i < matches.length; i++) {
    colSpecs.push({
      name: matches[i].name,
      start: matches[i].start,
      end: i + 1 < matches.length ? matches[i + 1].start : Number.POSITIVE_INFINITY,
    });
  }
  return lines.slice(1).map((line) => {
    const row: Record<string, string> = {};
    for (const c of colSpecs) {
      row[c.name] = line.slice(c.start, Math.min(c.end, line.length)).trim();
    }
    return row;
  });
}

function PodsTable({
  namespace,
  onLogs,
  onDescribe,
  onRefresh,
}: {
  namespace: string;
  onLogs: (name: string, text: string) => void;
  onDescribe: (name: string, text: string) => void;
  onRefresh: () => void;
}): JSX.Element {
  const [rows, setRows] = useState<Pod[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .invokeTool("sysadmin", "get_pods", { namespace })
      .then((r) => {
        if (cancelled) return;
        const parsed = parseKubectlTable(String(r.result ?? ""));
        setRows(parsed.map((row) => ({
          name: row.NAME ?? "", ready: row.READY ?? "",
          status: row.STATUS ?? "", restarts: row.RESTARTS ?? "",
          age: row.AGE ?? "", ip: row.IP ?? "", node: row.NODE ?? "",
        })));
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [namespace]);

  const fetchLogs = async (name: string): Promise<void> => {
    setActionLoading(`logs:${name}`);
    try {
      const r = await api.invokeTool("sysadmin", "get_logs", { pod: name, namespace, tail_lines: 200 });
      onLogs(name, String(r.result ?? ""));
    } catch (e) { onLogs(name, `error: ${(e as Error).message}`); }
    finally { setActionLoading(null); }
  };
  const fetchDescribe = async (name: string): Promise<void> => {
    setActionLoading(`describe:${name}`);
    try {
      const r = await api.invokeTool("sysadmin", "describe_pod", { name, namespace });
      onDescribe(name, String(r.result ?? ""));
    } catch (e) { onDescribe(name, `error: ${(e as Error).message}`); }
    finally { setActionLoading(null); }
  };
  const deletePod = async (name: string): Promise<void> => {
    if (!window.confirm(`Delete pod ${name}?\nThis requires approval and is destructive.`)) return;
    setActionLoading(`delete:${name}`);
    try {
      await api.invokeTool("sysadmin", "delete_pod", { name, namespace });
      onRefresh();
    } catch (e) {
      alert(`delete_pod failed: ${(e as Error).message}`);
    } finally { setActionLoading(null); }
  };

  if (error) {
    return <div className="text-accent-red font-mono text-sm">error: {error}</div>;
  }
  if (rows === null) {
    return <div className="text-text-muted italic text-sm">loading pods…</div>;
  }

  const columns: Column<Pod>[] = [
    { key: "name", header: "Name", cell: (r) => <span className="font-mono text-[12.5px]">{r.name}</span> },
    {
      key: "status",
      header: "Status",
      cell: (r) => <Badge tone={k8sStatusTone(r.status)}>{r.status}</Badge>,
    },
    { key: "ready", header: "Ready", cell: (r) => <span className="font-mono text-xs">{r.ready}</span> },
    { key: "restarts", header: "Restarts", cell: (r) => <span className="font-mono text-xs">{r.restarts}</span> },
    { key: "age", header: "Age", cell: (r) => <span className="font-mono text-xs">{r.age}</span> },
    { key: "node", header: "Node", cell: (r) => <span className="font-mono text-xs text-text-secondary">{r.node}</span> },
    {
      key: "actions",
      header: "",
      align: "right",
      cell: (r) => (
        <div className="flex items-center gap-1 justify-end">
          <Button
            size="sm"
            variant="ghost"
            icon={<FileText size={12} />}
            loading={actionLoading === `logs:${r.name}`}
            onClick={(e) => { e.stopPropagation(); void fetchLogs(r.name); }}
          >
            logs
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<Eye size={12} />}
            loading={actionLoading === `describe:${r.name}`}
            onClick={(e) => { e.stopPropagation(); void fetchDescribe(r.name); }}
          >
            describe
          </Button>
          <Button
            size="sm"
            variant="danger"
            icon={<Trash2 size={12} />}
            loading={actionLoading === `delete:${r.name}`}
            onClick={(e) => { e.stopPropagation(); void deletePod(r.name); }}
          >
            delete
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="bg-dark-panel border border-border-subtle rounded-md">
      <Table columns={columns} rows={rows} rowKey={(r) => r.name} empty={`no pods in ${namespace}`} />
    </div>
  );
}

// ----- nodes -----

function NodesTable(): JSX.Element {
  const [rows, setRows] = useState<NodeRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .invokeTool("sysadmin", "get_nodes", {})
      .then((r) => {
        if (cancelled) return;
        const parsed = parseKubectlTable(String(r.result ?? ""));
        setRows(parsed.map((row) => ({
          name: row.NAME ?? "", status: row.STATUS ?? "",
          roles: row.ROLES ?? "", age: row.AGE ?? "", version: row.VERSION ?? "",
        })));
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, []);
  if (error) return <div className="text-accent-red font-mono text-sm">error: {error}</div>;
  if (rows === null) return <div className="text-text-muted italic text-sm">loading nodes…</div>;
  const columns: Column<NodeRow>[] = [
    { key: "name", header: "Name", cell: (r) => <span className="font-mono text-[12.5px]">{r.name}</span> },
    {
      key: "status", header: "Status",
      cell: (r) => <Badge tone={k8sStatusTone(r.status)}>{r.status}</Badge>,
    },
    { key: "roles", header: "Roles", cell: (r) => <span className="font-mono text-xs">{r.roles}</span> },
    { key: "age", header: "Age", cell: (r) => <span className="font-mono text-xs">{r.age}</span> },
    { key: "version", header: "Version", cell: (r) => <span className="font-mono text-xs text-text-secondary">{r.version}</span> },
  ];
  return (
    <div className="bg-dark-panel border border-border-subtle rounded-md">
      <Table columns={columns} rows={rows} rowKey={(r) => r.name} empty="no nodes" />
    </div>
  );
}

// ----- events -----

function EventsTable({ namespace }: { namespace: string }): JSX.Element {
  const [rows, setRows] = useState<EventRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .invokeTool("sysadmin", "get_events", { namespace })
      .then((r) => {
        if (cancelled) return;
        const parsed = parseKubectlTable(String(r.result ?? ""));
        setRows(parsed.map((row) => ({
          last_seen: row["LAST"] ?? row["LASTSEEN"] ?? row["AGE"] ?? "",
          type: row.TYPE ?? "",
          reason: row.REASON ?? "",
          object: row.OBJECT ?? "",
          message: row.MESSAGE ?? "",
        })));
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [namespace]);
  if (error) return <div className="text-accent-red font-mono text-sm">error: {error}</div>;
  if (rows === null) return <div className="text-text-muted italic text-sm">loading events…</div>;
  const columns: Column<EventRow>[] = [
    { key: "last_seen", header: "Last seen", cell: (r) => <span className="font-mono text-xs">{r.last_seen}</span> },
    {
      key: "type", header: "Type",
      cell: (r) => <Badge tone={r.type === "Warning" ? "yellow" : "blue"}>{r.type}</Badge>,
    },
    { key: "reason", header: "Reason", cell: (r) => <span className="font-mono text-xs">{r.reason}</span> },
    { key: "object", header: "Object", cell: (r) => <span className="font-mono text-xs text-text-secondary">{r.object}</span> },
    { key: "message", header: "Message", cell: (r) => <span className="text-xs">{r.message}</span> },
  ];
  return (
    <div className="bg-dark-panel border border-border-subtle rounded-md">
      <Table columns={columns} rows={rows} rowKey={(r) => `${r.object}-${r.reason}-${r.last_seen}`} empty="no recent events" />
    </div>
  );
}
