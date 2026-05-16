import { useState } from "react";
import { Copy, Check } from "lucide-react";
import clsx from "clsx";

interface CodeBlockProps {
  text: string;
  language?: string;
  className?: string;
  maxHeight?: string;
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

  return (
    <div className={clsx("relative group", className)}>
      <pre
        className="bg-dark-primary border border-border-subtle rounded-md p-3 pr-12 overflow-auto text-[12.5px] leading-relaxed font-mono text-text-primary"
        style={{ maxHeight }}
      >
        <code>{text || "(empty)"}</code>
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
