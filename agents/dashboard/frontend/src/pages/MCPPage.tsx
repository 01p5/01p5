import { useEffect, useState } from "react";
import { Plug, AlertCircle, ChevronRight, ShieldAlert, Plus, X, Trash2 } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";
import { usePolling } from "../hooks/usePolling";
import type { AddMCPServerRequest, MCPServerSummary, MCPToolDescriptor } from "../types";

/**
 * MCP — third-party tool servers wired into Olympus.
 *
 * Each MCP server's tools are registered onto a target agent,
 * prefixed with the server name (so two servers can both declare a
 * tool called "read" without collisions). This page shows what's
 * wired, lets the user inspect each tool's description + arg
 * schema, and supports runtime add (the "Add server" button) +
 * runtime disconnect (the trash icon on each card).
 *
 * Mutations are not persisted across dashboard restarts — they live
 * only in the in-memory registry. To survive restarts, declare the
 * server in build_default_server's mcp_servers parameter.
 */
export function MCPPage(): JSX.Element {
  const { data, error, refresh } = usePolling(api.listMcpServers, 4000);
  const servers = data ?? [];
  const [showAddForm, setShowAddForm] = useState(false);

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
            onClick={() => setShowAddForm((v) => !v)}
            className="ml-auto inline-flex items-center gap-1 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-green hover:text-accent-green-bright px-2 py-1 border border-accent-green/40 hover:border-accent-green/70 rounded transition-colors"
            aria-expanded={showAddForm}
          >
            {showAddForm ? <X size={11} /> : <Plus size={11} />}
            {showAddForm ? "cancel" : "add server"}
          </button>
          <button
            onClick={refresh}
            className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-accent-blue px-2 py-1 border border-border-subtle hover:border-accent-blue/40 rounded transition-colors"
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
        {showAddForm && (
          <AddServerForm
            onAdded={() => { setShowAddForm(false); refresh(); }}
            onCancel={() => setShowAddForm(false)}
          />
        )}
        {error && (
          <div className="flex items-start gap-2 text-accent-red bg-accent-red/10 border border-accent-red/30 rounded-md px-4 py-3 text-sm">
            <AlertCircle size={16} className="mt-0.5" />
            <div>
              <div className="font-semibold">Couldn't load MCP servers</div>
              <code className="text-[11px] break-all">{error.message}</code>
            </div>
          </div>
        )}
        {!error && servers.length === 0 && !showAddForm && <EmptyState />}
        {servers.map((s) => (
          <ServerCard key={s.name} server={s} onChanged={refresh} />
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
  onChanged?: () => void;
}

export function ServerCard({ server, onChanged }: CardProps): JSX.Element {
  const isErr = server.status === "error";
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const onDisconnect = async (): Promise<void> => {
    if (!window.confirm(
      `Disconnect MCP server "${server.name}"? Its tools will be removed from the ${server.target_agent} agent.`,
    )) {
      return;
    }
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteMcpServer(server.name);
      onChanged?.();
    } catch (e) {
      setDeleteError((e as Error).message);
      setDeleting(false);
    }
  };

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
        <button
          onClick={() => void onDisconnect()}
          disabled={deleting}
          aria-label={`disconnect ${server.name}`}
          className="mcp-disconnect-btn inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted hover:text-accent-red px-1.5 py-0.5 border border-border-subtle hover:border-accent-red/40 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Trash2 size={10} />
          {deleting ? "removing…" : "disconnect"}
        </button>
      </div>
      {deleteError && (
        <div className="px-4 py-1.5 border-b border-border-subtle/60 text-[10px] font-mono text-accent-red">
          disconnect failed: {deleteError}
        </div>
      )}
      {server.command && (
        <div
          className="px-4 py-2 border-b border-border-subtle/60 text-[11px] font-mono text-text-secondary truncate"
          data-transport={server.command.startsWith("HTTP ") ? "http" : "stdio"}
        >
          {/* `$` reads as a shell prompt for stdio; `→` reads as an
              endpoint pointer for HTTP. Both keep the row scannable. */}
          <span className="text-text-muted">
            {server.command.startsWith("HTTP ") ? "→" : "$"}
          </span>{" "}
          {server.command}
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


/** Inline add-server form. Picks transport mode then renders the
 *  right field set. Submits via api.addMcpServer; on success calls
 *  onAdded so the parent can refresh + collapse. */
interface AddFormProps {
  onAdded: () => void;
  onCancel: () => void;
}

function AddServerForm({ onAdded, onCancel }: AddFormProps): JSX.Element {
  const [name, setName] = useState("");
  const [targetAgent, setTargetAgent] = useState("programmer");
  const [transport, setTransport] = useState<"stdio" | "http">("stdio");
  // stdio fields
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  // http fields
  const [url, setUrl] = useState("");
  const [headersText, setHeadersText] = useState("");
  // common
  const [destructiveText, setDestructiveText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    const req: AddMCPServerRequest = {
      name: name.trim(),
      target_agent: targetAgent.trim(),
      transport,
      destructive: destructiveText
        .split(",").map((s) => s.trim()).filter(Boolean),
    };
    if (transport === "stdio") {
      req.command = command.trim();
      // Tokenize args on whitespace, preserving "quoted strings"
      // as a single arg. Good enough for MCP server invocations.
      req.args = argsText
        .match(/(?:"[^"]*"|\S)+/g)
        ?.map((a) => a.replace(/^"|"$/g, "")) ?? [];
    } else {
      req.url = url.trim();
      // headers textarea is "Key: value" per line.
      const headers: Record<string, string> = {};
      for (const line of headersText.split("\n")) {
        const idx = line.indexOf(":");
        if (idx < 0) continue;
        const k = line.slice(0, idx).trim();
        const v = line.slice(idx + 1).trim();
        if (k) headers[k] = v;
      }
      req.headers = headers;
    }
    try {
      await api.addMcpServer(req);
      onAdded();
    } catch (e) {
      setError((e as Error).message);
      setSubmitting(false);
    }
  };

  return (
    <form
      onSubmit={(e) => void onSubmit(e)}
      data-testid="add-mcp-form"
      className="add-mcp-form rounded-md border border-accent-blue/40 bg-dark-secondary/60 p-4 space-y-3"
    >
      <div className="flex items-center gap-2">
        <Plus size={14} className="text-accent-blue" />
        <h3 className="font-display text-sm font-semibold text-text-primary">
          Add MCP server
        </h3>
      </div>
      <div className="grid grid-cols-2 gap-3 text-[12px] font-mono">
        <Field label="name (used as tool prefix)" value={name} onChange={setName} placeholder="github" required />
        <Field label="target agent" value={targetAgent} onChange={setTargetAgent} placeholder="programmer" required />
      </div>

      <div className="text-[11px] font-mono text-text-secondary flex items-center gap-3 pt-1">
        transport:
        <label className="inline-flex items-center gap-1">
          <input
            type="radio" value="stdio"
            checked={transport === "stdio"}
            onChange={() => setTransport("stdio")}
          />
          stdio (subprocess)
        </label>
        <label className="inline-flex items-center gap-1">
          <input
            type="radio" value="http"
            checked={transport === "http"}
            onChange={() => setTransport("http")}
          />
          http (remote)
        </label>
      </div>

      {transport === "stdio" ? (
        <div className="space-y-2" data-transport-fields="stdio">
          <Field label="command" value={command} onChange={setCommand} placeholder="python3" required />
          <Field label="args (space-separated, &quot;quotes&quot; for spaces)" value={argsText} onChange={setArgsText} placeholder="-m mymodule --flag value" />
        </div>
      ) : (
        <div className="space-y-2" data-transport-fields="http">
          <Field label="url" value={url} onChange={setUrl} placeholder="https://mcp.example.com/server" required />
          <TextArea
            label="headers (one per line, Key: value)"
            value={headersText}
            onChange={setHeadersText}
            placeholder="Authorization: Bearer ..."
            rows={2}
          />
        </div>
      )}

      <div className="space-y-1">
        <label className="text-[11px] font-mono text-text-muted">
          destructive tools (comma-separated, unprefixed names)
        </label>
        <input
          type="text"
          value={destructiveText}
          onChange={(e) => setDestructiveText(e.target.value)}
          placeholder="write_file, delete_branch"
          className="w-full bg-dark-panel border border-border-subtle rounded px-2 py-1 text-[12px] font-mono text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60"
        />
        <p className="text-[10px] font-mono text-text-muted">
          Anything listed here routes through the approval queue
          before firing. The server doesn't get to decide what's
          destructive — you do.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-1.5 text-[11px] font-mono text-accent-red">
          <AlertCircle size={11} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <button
          type="submit"
          disabled={submitting}
          className="add-mcp-submit text-[11px] font-mono uppercase tracking-[1.5px] text-dark-primary bg-accent-green hover:bg-accent-green-bright disabled:opacity-40 px-3 py-1 rounded transition-colors"
        >
          {submitting ? "connecting…" : "register"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-text-primary px-3 py-1 border border-border-subtle hover:border-border-strong rounded transition-colors"
        >
          cancel
        </button>
      </div>
    </form>
  );
}

interface FieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
}

function Field({ label, value, onChange, placeholder, required }: FieldProps): JSX.Element {
  return (
    <label className="block text-[11px] font-mono text-text-muted">
      {label}{required && <span className="text-accent-red">*</span>}
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        className="mt-0.5 w-full bg-dark-panel border border-border-subtle rounded px-2 py-1 text-[12px] font-mono text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60"
      />
    </label>
  );
}

interface TextAreaProps extends FieldProps {
  rows?: number;
}

function TextArea({ label, value, onChange, placeholder, rows = 3 }: TextAreaProps): JSX.Element {
  return (
    <label className="block text-[11px] font-mono text-text-muted">
      {label}
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={rows}
        className="mt-0.5 w-full bg-dark-panel border border-border-subtle rounded px-2 py-1 text-[12px] font-mono text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60 resize-y"
      />
    </label>
  );
}
