import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Cpu, Server, Activity, RefreshCw, AlertCircle, Sparkles } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";


/**
 * HPC page — pre-rendered views over the gpu-mcp + slurm-mcp output.
 *
 * Both MCP servers must be connected to the ``hpc`` agent before this
 * page can render anything (the agent's prerequisites enforce that on
 * the routing side; here we surface the same gate visually + nudge
 * the user to the MCP integration card if either is missing).
 *
 * Once connected, each section auto-loads its primary tools via
 * POST /tools/hpc/<prefixed_tool> and renders the text-content reply
 * in a <pre> block. Per-section refresh button re-fetches everything
 * in that section.
 */
export function HPCPage(): JSX.Element {
  const { data: servers } = usePolling(api.listMcpServers, 5000);
  const gpu = (servers ?? []).find((s) => s.name === "gpu-mcp");
  const slurm = (servers ?? []).find((s) => s.name === "slurm-mcp");
  const gpuOk = gpu?.status === "connected";
  const slurmOk = slurm?.status === "connected";

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 py-3 border-b border-border-subtle bg-dark-secondary/40 flex items-baseline gap-3">
        <Cpu size={16} className="text-accent-green self-center" strokeWidth={2.25} />
        <h1 className="font-display text-base font-semibold text-text-primary">HPC</h1>
        <span className="text-[11px] font-mono text-text-muted">
          slurm scheduler + GPU health · powered by 2 MCP servers
        </span>
      </div>

      <div className="flex-1 overflow-auto px-6 py-6 max-w-6xl mx-auto w-full space-y-5">
        <ConnectionStatus gpuOk={gpuOk} slurmOk={slurmOk} />

        {slurmOk && <SlurmSection />}
        {gpuOk && <GpuSection />}
      </div>
    </section>
  );
}


function ConnectionStatus({ gpuOk, slurmOk }: { gpuOk: boolean; slurmOk: boolean }): JSX.Element | null {
  if (gpuOk && slurmOk) {
    return (
      <div
        data-testid="hpc-connection-ok"
        className="flex items-center gap-2 px-3 py-1.5 bg-accent-green/[0.06] border border-accent-green/30 rounded text-[11px] font-mono text-text-secondary"
      >
        <Sparkles size={12} className="text-accent-green" strokeWidth={2.5} />
        gpu-mcp + slurm-mcp connected — live cluster view below
      </div>
    );
  }
  return (
    <div
      data-testid="hpc-not-connected"
      className="bg-accent-yellow/[0.06] border border-accent-yellow/40 rounded-md p-4 space-y-2"
    >
      <div className="flex items-center gap-2">
        <AlertCircle size={16} className="text-accent-yellow" strokeWidth={2.25} />
        <span className="font-semibold text-text-primary text-sm">HPC integration not wired</span>
      </div>
      <p className="text-[12px] text-text-secondary leading-snug">
        The HPC agent needs both <code>gpu-mcp</code> and <code>slurm-mcp</code> MCP
        servers connected before this page (and the agent's routing
        availability) light up. Status: <code>{slurmOk ? "✓ slurm-mcp" : "✗ slurm-mcp"}</code>,{" "}
        <code>{gpuOk ? "✓ gpu-mcp" : "✗ gpu-mcp"}</code>.
      </p>
      <Link
        to="/mcp"
        className="inline-flex items-center gap-1.5 mt-1 px-3 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-blue border border-accent-blue/40 hover:bg-accent-blue/10 rounded"
      >
        Open MCP integration card
      </Link>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Slurm section — schedule + queue + accounting
// ---------------------------------------------------------------------------


function SlurmSection(): JSX.Element {
  return (
    <Section title="Slurm scheduler" icon={Server} accent="text-accent-blue" testId="hpc-slurm-section">
      <ToolCard label="Nodes"            tool="slurm-mcp_nodes_list"       testId="hpc-slurm-nodes" />
      <ToolCard label="Jobs (queue)"     tool="slurm-mcp_jobs_list"        testId="hpc-slurm-jobs" />
      <ToolCard label="Partitions"       tool="slurm-mcp_partitions_list"  testId="hpc-slurm-partitions" />
      <ToolCard label="Diagnostics"      tool="slurm-mcp_diagnostics_show" testId="hpc-slurm-diagnostics" />
    </Section>
  );
}


// ---------------------------------------------------------------------------
// GPU section — fleet health + drain advisor + per-node detail
// ---------------------------------------------------------------------------


function GpuSection(): JSX.Element {
  return (
    <Section title="GPU health" icon={Activity} accent="text-accent-green" testId="hpc-gpu-section">
      <ToolCard label="Nodes"          tool="gpu-mcp_nodes_list"     testId="hpc-gpu-nodes" />
      <ToolCard label="Fleet summary"  tool="gpu-mcp_fleet_summary"  testId="hpc-gpu-fleet" />
      <ToolCard label="Drain advisor"  tool="gpu-mcp_drain_advisor"  testId="hpc-gpu-drain" />
      <NodeStatusCard />
    </Section>
  );
}


function NodeStatusCard(): JSX.Element {
  const [node, setNode] = useState("gpu-n01");
  return (
    <ToolCard
      label="Per-node detail"
      tool="gpu-mcp_node_status"
      args={{ node }}
      testId="hpc-gpu-node-status"
      extraHeader={
        <select
          value={node}
          onChange={(e) => setNode(e.target.value)}
          data-testid="hpc-gpu-node-select"
          className="bg-dark-primary border border-border-subtle rounded px-2 py-0.5 text-[11px] font-mono text-text-primary focus:outline-none focus:border-accent-green/60"
        >
          {["gpu-n01", "gpu-n02", "gpu-n03", "gpu-n04"].map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
      }
    />
  );
}


// ---------------------------------------------------------------------------
// Generic primitives
// ---------------------------------------------------------------------------


function Section({
  title, icon: Icon, accent, testId, children,
}: {
  title: string;
  icon: typeof Server;
  accent: string;
  testId: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div data-testid={testId} className="space-y-2">
      <div className="flex items-center gap-2 px-1">
        <Icon size={14} className={accent} strokeWidth={2.5} />
        <h2 className={clsx("font-display text-[11px] font-semibold uppercase tracking-[1.5px]", accent)}>
          {title}
        </h2>
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}


function ToolCard({
  label, tool, testId, args, extraHeader,
}: {
  label: string;
  tool: string;
  testId: string;
  args?: Record<string, unknown>;
  extraHeader?: React.ReactNode;
}): JSX.Element {
  const [output, setOutput] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.invokeTool("hpc", tool, args ?? {});
      setOutput(extractText(r.result));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [tool, JSON.stringify(args ?? {})]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <div
      data-testid={testId}
      className="bg-dark-panel border border-border-subtle rounded-md"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border-subtle/60">
        <span className="font-display text-[11px] font-semibold text-text-primary">{label}</span>
        <code className="text-[10px] font-mono text-text-muted">{tool}</code>
        <div className="flex-1" />
        {extraHeader}
        <button
          onClick={() => void refresh()}
          disabled={loading}
          aria-label="Refresh"
          data-testid={`${testId}-refresh`}
          className={clsx(
            "p-1 text-text-muted hover:text-text-primary transition-colors",
            loading && "animate-spin",
          )}
        >
          <RefreshCw size={14} strokeWidth={2.25} />
        </button>
      </div>
      <div className="p-3">
        {error && (
          <div className="text-[11px] text-accent-red bg-accent-red/[0.06] border border-accent-red/30 rounded p-2 font-mono">
            {error}
          </div>
        )}
        {!error && !output && loading && (
          <div className="text-[11px] font-mono text-text-muted italic">loading…</div>
        )}
        {!error && output && (
          <pre className="font-mono text-[11px] leading-snug text-text-primary whitespace-pre overflow-auto max-h-96">
            {output}
          </pre>
        )}
      </div>
    </div>
  );
}


/** Extract the text body from an MCP-style tool result. The dashboard's
 *  /tools/{agent}/{tool} returns whatever the tool returned — for our
 *  MCP-backed tools that's a {content:[{type:"text", text:"..."}]} dict,
 *  but defensively handle plain-string + other shapes too. */
export function extractText(result: unknown): string {
  if (result == null) return "";
  if (typeof result === "string") return result;
  if (typeof result === "object") {
    const r = result as { content?: unknown; text?: unknown };
    if (typeof r.text === "string") return r.text;
    if (Array.isArray(r.content)) {
      return r.content
        .map((c) => {
          if (typeof c === "string") return c;
          if (c && typeof c === "object" && "text" in c && typeof (c as { text: unknown }).text === "string") {
            return (c as { text: string }).text;
          }
          return "";
        })
        .filter(Boolean)
        .join("\n");
    }
  }
  try { return JSON.stringify(result, null, 2); } catch { return String(result); }
}
