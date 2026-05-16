import { useEffect, useState } from "react";
import { Plug, AlertCircle, ChevronRight, ShieldAlert } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import type { MCPServerSummary, MCPToolDescriptor } from "../types";

/**
 * MCP — third-party tool servers wired into Olympus.
 *
 * Each MCP server's tools are registered onto a target agent at
 * dashboard startup, prefixed with the server name (so two servers
 * can both declare a tool called "read" without collisions). This
 * page shows what's wired and lets the user inspect each tool's
 * description + arg schema.
 *
 * Read-only in v1 — adding/removing servers at runtime is a follow-up.
 */
export function MCPPage(): JSX.Element {
  const { data, error, refresh } = usePolling(api.listMcpServers, 4000);
  const servers = data ?? [];

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary overflow-hidden">
      <div className="px-6 py-4 border-b border-border-subtle bg-dark-secondary/40">
        <div className="flex items-center gap-3">
          <Plug size={18} className="text-accent-blue" strokeWidth={2.25} />
          <h1 className="font-display text-lg font-semibold text-text-primary">
            MCP servers
          </h1>
          <span className="text-[11px] font-mono text-text-muted">
            {servers.length} wired · third-party tool providers
          </span>
          <button
            onClick={refresh}
            className="ml-auto text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-blue px-2 py-1 border border-border-subtle hover:border-accent-blue/40 rounded transition-colors"
          >
            refresh
          </button>
        </div>
        <p className="text-[12px] font-mono text-text-muted mt-2">
          Each server's tools register onto a target agent with a prefix.
          Destructive verbs go through the same approval queue as
          native tools — flagged at registration time, not by the server.
        </p>
      </div>
      <div className="flex-1 overflow-auto px-6 py-5 space-y-4">
        {error && (
          <div className="flex items-start gap-2 text-accent-red bg-accent-red/10 border border-accent-red/30 rounded-md px-4 py-3 text-sm">
            <AlertCircle size={16} className="mt-0.5" />
            <div>
              <div className="font-semibold">Couldn't load MCP servers</div>
              <code className="text-[11px] break-all">{error.message}</code>
            </div>
          </div>
        )}
        {!error && servers.length === 0 && <EmptyState />}
        {servers.map((s) => (
          <ServerCard key={s.name} server={s} />
        ))}
      </div>
    </section>
  );
}

function EmptyState(): JSX.Element {
  return (
    <div className="max-w-3xl mx-auto py-10 text-center space-y-4">
      <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-accent-blue/10 border border-accent-blue/30">
        <Plug size={20} className="text-accent-blue" />
      </div>
      <h2 className="font-display text-xl font-semibold text-text-primary">
        No MCP servers wired
      </h2>
      <p className="text-sm text-text-secondary max-w-md mx-auto">
        Olympus is running with only its native agents. To extend an
        agent's tool set, configure an MCP server at startup via the
        <code className="font-mono text-text-primary mx-1 px-1.5 py-0.5 rounded bg-dark-panel">
          mcp_servers
        </code>
        parameter on
        <code className="font-mono text-text-primary mx-1 px-1.5 py-0.5 rounded bg-dark-panel">
          build_default_server
        </code>
        .
      </p>
    </div>
  );
}

interface CardProps {
  server: MCPServerSummary;
}

export function ServerCard({ server }: CardProps): JSX.Element {
  const isErr = server.status === "error";
  return (
    <div
      data-server-name={server.name}
      data-server-status={server.status}
      className={clsx(
        "mcp-server-card rounded-md border bg-dark-panel",
        isErr
          ? "border-accent-red/40 bg-accent-red/[0.05]"
          : "border-border-subtle",
      )}
    >
      <div className="px-4 py-3 border-b border-border-subtle/60 flex items-baseline gap-3 flex-wrap">
        <h3 className="font-display text-base font-semibold text-text-primary">
          {server.name}
        </h3>
        <span className="text-[11px] font-mono text-text-muted">
          → {server.target_agent ?? "?"}
        </span>
        <span
          className={clsx(
            "text-[10px] font-mono uppercase tracking-[1.5px] px-2 py-0.5 rounded border",
            isErr
              ? "text-accent-red border-accent-red/40 bg-accent-red/10"
              : "text-accent-green border-accent-green/40 bg-accent-green/10",
          )}
        >
          {server.status}
        </span>
        <span className="text-[10px] font-mono text-text-muted ml-auto">
          {server.tool_count} tool{server.tool_count === 1 ? "" : "s"}
        </span>
      </div>
      {server.command && (
        <div className="px-4 py-2 border-b border-border-subtle/60 text-[11px] font-mono text-text-secondary truncate">
          <span className="text-text-muted">$</span> {server.command}
        </div>
      )}
      {server.error && (
        <div className="px-4 py-2 border-b border-border-subtle/60 flex items-start gap-2 text-[11px] font-mono text-accent-red">
          <AlertCircle size={11} className="mt-0.5 shrink-0" />
          <span className="break-all">{server.error}</span>
        </div>
      )}
      {!isErr && <ToolListing server={server} />}
    </div>
  );
}

function ToolListing({ server }: CardProps): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [catalog, setCatalog] = useState<MCPToolDescriptor[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!expanded || catalog !== null) return;
    let cancelled = false;
    const run = async (): Promise<void> => {
      try {
        const got = await api.getMcpServerTools(server.name);
        if (!cancelled) setCatalog(got.tools);
      } catch (e) {
        if (!cancelled) setLoadError((e as Error).message);
      }
    };
    void run();
    return () => { cancelled = true; };
  }, [expanded, catalog, server.name]);

  return (
    <div>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full px-4 py-2.5 text-left text-[12px] font-mono text-text-secondary hover:text-text-primary flex items-center gap-2 transition-colors"
        aria-expanded={expanded}
      >
        <ChevronRight
          size={12}
          strokeWidth={2.5}
          className={clsx(
            "transition-transform",
            expanded && "rotate-90",
          )}
        />
        {expanded ? "hide" : "show"} {server.tool_count} tool{server.tool_count === 1 ? "" : "s"}
      </button>
      {expanded && (
        <div className="px-4 pb-4 space-y-2">
          {loadError && (
            <div className="text-[11px] text-accent-red font-mono">
              load failed: {loadError}
            </div>
          )}
          {catalog === null && !loadError && (
            <div className="text-[11px] text-text-muted font-mono">loading…</div>
          )}
          {catalog?.map((tool) => (
            <ToolRow
              key={tool.name}
              tool={tool}
              isDestructive={server.destructive.includes(`${server.name}_${tool.name}`)}
              prefix={server.name}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface ToolRowProps {
  tool: MCPToolDescriptor;
  isDestructive: boolean;
  prefix: string;
}

function ToolRow({ tool, isDestructive, prefix }: ToolRowProps): JSX.Element {
  return (
    <div
      data-tool-name={tool.name}
      data-destructive={isDestructive ? "1" : "0"}
      className={clsx(
        "mcp-tool rounded border px-3 py-2 text-[12px] font-mono",
        isDestructive
          ? "border-accent-yellow/40 bg-accent-yellow/[0.05]"
          : "border-border-subtle bg-dark-secondary/40",
      )}
    >
      <div className="flex items-baseline gap-2">
        <code className="text-text-primary font-semibold">
          {prefix}_{tool.name}
        </code>
        {isDestructive && (
          <span className="inline-flex items-center gap-0.5 text-[9px] uppercase tracking-[1.5px] text-accent-yellow">
            <ShieldAlert size={9} />
            destructive
          </span>
        )}
      </div>
      <p className="text-text-secondary mt-1">{tool.description}</p>
    </div>
  );
}
