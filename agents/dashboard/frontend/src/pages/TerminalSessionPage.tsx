import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, AlertCircle, Wifi, WifiOff, Sparkles, Send, PanelRightClose, PanelRightOpen } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import clsx from "clsx";
import { api } from "../api";


type ConnState = "connecting" | "open" | "closed" | "error";


/**
 * TERM.3b — live xterm.js terminal bridged to the dashboard's PTY via
 * WebSocket. Mounts on /terminal/{sessionId}.
 *
 * Lifecycle: dial ``ws(s)://{host}/terminal/sessions/{id}/ws`` on mount,
 * pipe browser keystrokes through ``onData`` → ws.send(BINARY), pipe
 * ws.onmessage bytes → term.write. On WS close show a "disconnected"
 * overlay with the close reason so the user knows whether to reconnect.
 * On component unmount: close the WS (the bridge thread on the server
 * detaches the session, which starts the 30-min grace clock).
 *
 * No xterm fancy features — fit-on-resize + binary frames only.
 * Command-injection markers (TERM.5) parse here in a later commit.
 */
export function TerminalSessionPage(): JSX.Element {
  const { sessionId } = useParams<{ sessionId: string }>();
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [state, setState] = useState<ConnState>("connecting");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [companionOpen, setCompanionOpen] = useState(true);

  useEffect(() => {
    if (!sessionId || !containerRef.current) return;

    const term = new Terminal({
      convertEol: true,
      fontFamily: "ui-monospace, monospace",
      fontSize: 13,
      theme: { background: "#0a0e14", foreground: "#cdd6f4" },
      scrollback: 5000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    const url = wsUrlFor(sessionId);
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => setState("open");
    ws.onmessage = (ev: MessageEvent<ArrayBuffer | string>) => {
      // Server always emits binary; tolerate text for safety.
      if (ev.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(ev.data));
      } else if (typeof ev.data === "string") {
        term.write(ev.data);
      }
    };
    ws.onclose = (ev) => {
      setState("closed");
      setErrorText(ev.reason || `code ${ev.code}`);
    };
    ws.onerror = () => {
      setState("error");
      setErrorText("WebSocket error — check that the session is still alive.");
    };

    // Browser keystrokes → server. xterm emits utf-8 strings.
    const inputDispose = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(new TextEncoder().encode(data));
      }
    });

    // Fit on window resize so a maximized window uses every column.
    // Also observe the terminal's own container so collapsing the
    // companion panel (which widens us) triggers a refit.
    const onResize = (): void => { try { fit.fit(); } catch { /* ignore */ } };
    window.addEventListener("resize", onResize);
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined" && containerRef.current) {
      ro = new ResizeObserver(onResize);
      ro.observe(containerRef.current);
    }

    return () => {
      window.removeEventListener("resize", onResize);
      ro?.disconnect();
      inputDispose.dispose();
      try { ws.close(); } catch { /* ignore */ }
      term.dispose();
      wsRef.current = null;
      termRef.current = null;
      fitRef.current = null;
    };
  }, [sessionId]);

  return (
    <section className="flex flex-col min-h-0 h-full bg-dark-primary">
      <div className="px-6 py-3 border-b border-border-subtle bg-dark-secondary/40 flex items-center gap-3">
        <Link to="/terminal" className="flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-[1.5px] text-text-secondary hover:text-text-primary">
          <ArrowLeft size={14} strokeWidth={2.25} />
          Sessions
        </Link>
        <span className="text-text-muted">·</span>
        <h1 className="font-mono text-sm text-text-primary">
          session <code>{sessionId?.slice(0, 12)}</code>
        </h1>
        <div className="flex-1" />
        <ConnPill state={state} />
      </div>

      <div className="flex-1 min-h-0 flex">
        <div className="flex-1 min-w-0 relative bg-[#0a0e14]">
          <div ref={containerRef} data-testid="xterm-container" className="absolute inset-0 p-2" />
          {(state === "closed" || state === "error") && (
            <div className="absolute inset-0 flex items-center justify-center bg-dark-primary/70 backdrop-blur-sm">
              <div className="bg-dark-panel border border-accent-red/40 rounded-md px-4 py-3 max-w-md text-center space-y-2">
                <AlertCircle size={20} className="mx-auto text-accent-red" strokeWidth={2.25} />
                <div className="text-sm font-semibold text-text-primary">
                  {state === "error" ? "Connection error" : "Session closed"}
                </div>
                {errorText && (
                  <div className="text-[11px] font-mono text-text-muted">{errorText}</div>
                )}
                <Link to="/terminal"
                      className="inline-block mt-2 px-3 py-1 text-[11px] font-mono uppercase tracking-[1.5px] text-accent-blue border border-accent-blue/40 hover:bg-accent-blue/10 rounded">
                  Back to sessions
                </Link>
              </div>
            </div>
          )}
        </div>
        {sessionId && companionOpen && (
          <CompanionPanel sessionId={sessionId} onClose={() => setCompanionOpen(false)} />
        )}
        {!companionOpen && (
          <button
            onClick={() => setCompanionOpen(true)}
            data-testid="companion-open"
            className="absolute right-3 top-20 z-10 flex items-center gap-1.5 px-2.5 py-1 text-[10px] font-mono uppercase tracking-[1.5px] text-accent-green bg-dark-panel border border-accent-green/40 hover:bg-accent-green/10 rounded transition-colors"
            title="Open the companion side panel"
          >
            <PanelRightOpen size={14} strokeWidth={2.5} />
            Companion
          </button>
        )}
      </div>
    </section>
  );
}


/** TERM.4c — pull-based LLM companion side panel.
 *
 * Vertical chat-like surface: scrollable Q&A history at top, input at
 * bottom. Each entry shows the user's question + the companion's
 * answer + any suggested next-command one-liners as copyable code
 * blocks. Hidden by default if the user toggles it off (state lives
 * in the parent so collapse re-fits the xterm).
 */
function CompanionPanel({
  sessionId,
  onClose,
}: {
  sessionId: string;
  onClose: () => void;
}): JSX.Element {
  type Entry = { question: string; answer?: string; suggestions?: string[]; error?: string };
  const [history, setHistory] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [history, busy]);

  const submit = async (e?: React.FormEvent): Promise<void> => {
    if (e) e.preventDefault();
    const q = input.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    const entry: Entry = { question: q };
    setHistory((prev) => [...prev, entry]);
    try {
      const r = await api.askTerminalCompanion(sessionId, q);
      setHistory((prev) => prev.map((p) =>
        p === entry ? { ...p, answer: r.answer, suggestions: r.suggested_commands } : p,
      ));
    } catch (err) {
      setHistory((prev) => prev.map((p) =>
        p === entry ? { ...p, error: (err as Error).message } : p,
      ));
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside
      data-testid="companion-panel"
      className="w-[360px] flex-shrink-0 flex flex-col min-h-0 bg-dark-secondary border-l border-border-subtle"
    >
      <div className="px-3 py-2.5 border-b border-border-subtle flex items-center gap-2">
        <Sparkles size={14} className="text-accent-green" strokeWidth={2.5} />
        <h2 className="font-display text-[11px] font-semibold uppercase tracking-[1.5px] text-text-secondary">
          Companion
        </h2>
        <span className="text-[10px] font-mono text-text-muted ml-auto">
          pull-based · reads on ask
        </span>
        <button
          onClick={onClose}
          aria-label="Hide companion"
          className="text-text-muted hover:text-text-primary"
          data-testid="companion-close"
        >
          <PanelRightClose size={14} strokeWidth={2.5} />
        </button>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-auto px-3 py-3 space-y-3 min-h-0">
        {history.length === 0 && (
          <div className="text-[11px] font-mono text-text-muted italic text-center py-6">
            Ask anything about this terminal session. The companion will
            read recent scrollback before answering.
          </div>
        )}
        {history.map((entry, i) => (
          <CompanionEntry key={i} entry={entry} />
        ))}
        {busy && history.length > 0 && (
          <div className="text-[10px] font-mono text-text-muted italic">…thinking</div>
        )}
      </div>

      <form onSubmit={(e) => void submit(e)} className="border-t border-border-subtle p-2 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="ask about this shell…"
          disabled={busy}
          data-testid="companion-input"
          className="flex-1 bg-dark-primary border border-border-subtle rounded px-2.5 py-1.5 text-[12px] text-text-primary focus:outline-none focus:border-accent-green/60 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          data-testid="companion-send"
          className="px-2.5 py-1.5 text-[10px] font-mono uppercase tracking-[1.5px] text-accent-green border border-accent-green/40 hover:bg-accent-green/10 rounded disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Send size={12} strokeWidth={2.5} />
        </button>
      </form>
    </aside>
  );
}


function CompanionEntry({ entry }: { entry: { question: string; answer?: string; suggestions?: string[]; error?: string } }): JSX.Element {
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] text-text-primary bg-accent-blue/[0.06] border border-accent-blue/20 rounded px-2.5 py-1.5">
        {entry.question}
      </div>
      {entry.error && (
        <div className="text-[11px] text-accent-red bg-accent-red/[0.06] border border-accent-red/30 rounded px-2.5 py-1.5">
          {entry.error}
        </div>
      )}
      {entry.answer !== undefined && (
        <div className="text-[12px] text-text-primary leading-snug bg-dark-panel border border-border-subtle rounded px-2.5 py-1.5 whitespace-pre-wrap">
          {entry.answer}
        </div>
      )}
      {entry.suggestions && entry.suggestions.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] font-mono uppercase tracking-[1.5px] text-text-muted">
            Suggested
          </div>
          {entry.suggestions.map((cmd, j) => (
            <pre key={j}
                 className="font-mono text-[11px] text-text-primary bg-dark-primary border border-border-subtle rounded px-2 py-1 overflow-auto whitespace-pre">
              {cmd}
            </pre>
          ))}
        </div>
      )}
    </div>
  );
}


/** Build the absolute ws(s):// URL the browser dials for a given
 *  session id. Uses the current page's protocol so dev (http+ws) and
 *  prod (https+wss) both work without config.
 *
 *  Exported for testing; production callers just hit it via the effect. */
export function wsUrlFor(sessionId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/terminal/sessions/${encodeURIComponent(sessionId)}/ws`;
}


function ConnPill({ state }: { state: ConnState }): JSX.Element {
  const map: Record<ConnState, { label: string; cls: string; Icon: typeof Wifi }> = {
    connecting: { label: "connecting…", cls: "text-text-muted border-border-subtle", Icon: Wifi },
    open:       { label: "connected",   cls: "text-accent-green border-accent-green/40", Icon: Wifi },
    closed:     { label: "closed",      cls: "text-accent-red border-accent-red/40", Icon: WifiOff },
    error:      { label: "error",       cls: "text-accent-red border-accent-red/40", Icon: WifiOff },
  };
  const { label, cls, Icon } = map[state];
  return (
    <span data-testid="conn-pill"
          className={clsx("flex items-center gap-1.5 px-2.5 py-1 text-[10px] font-mono uppercase tracking-[1.5px] border rounded", cls)}>
      <Icon size={12} strokeWidth={2.5} />
      {label}
    </span>
  );
}
