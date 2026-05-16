import { useState } from "react";
import { Copy, Check } from "lucide-react";
import clsx from "clsx";

interface CodeBlockProps {
  text: string;
  language?: string;
  className?: string;
  maxHeight?: string;
}

/** Lightweight inline syntax highlight for the only language where it
 * matters — unified diff. Splits the text into lines and tints each
 * by its leading marker. Falls back to plain `<code>` for anything else. */
function DiffHighlight({ text }: { text: string }): JSX.Element {
  const lines = text.split("\n");
  return (
    <code>
      {lines.map((line, i) => {
        let cls = "";
        if (line.startsWith("+++") || line.startsWith("---")) {
          cls = "text-text-muted";
        } else if (line.startsWith("@@")) {
          cls = "text-accent-blue";
        } else if (line.startsWith("+")) {
          cls = "text-accent-green bg-accent-green/[0.06]";
        } else if (line.startsWith("-")) {
          cls = "text-accent-red bg-accent-red/[0.06]";
        }
        return (
          <span key={i} className={clsx("block whitespace-pre", cls)}>
            {line || " "}
          </span>
        );
      })}
    </code>
  );
}

export function CodeBlock({
  text, language, className, maxHeight = "400px",
}: CodeBlockProps): JSX.Element {
  const [copied, setCopied] = useState(false);

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* ignore */ }
  };

  const isDiff = language === "diff";

  return (
    <div className={clsx("relative group", className)}>
      <pre
        className="bg-dark-primary border border-border-subtle rounded-md p-3 pr-12 overflow-auto text-[12.5px] leading-relaxed font-mono text-text-primary"
        style={{ maxHeight }}
      >
        {isDiff ? <DiffHighlight text={text || "(empty)"} /> : <code>{text || "(empty)"}</code>}
      </pre>
      {language && (
        <span className="absolute top-2 left-2 text-[10px] font-mono uppercase tracking-wider text-text-muted bg-dark-secondary/80 px-1.5 py-0.5 rounded">
          {language}
        </span>
      )}
      <button
        onClick={copy}
        className="absolute top-2 right-2 p-1.5 bg-dark-control/80 hover:bg-border-active border border-border-subtle rounded text-text-secondary hover:text-text-primary opacity-0 group-hover:opacity-100 transition-opacity"
        title={copied ? "copied" : "copy"}
      >
        {copied ? <Check size={12} className="text-accent-green" /> : <Copy size={12} />}
      </button>
    </div>
  );
}
