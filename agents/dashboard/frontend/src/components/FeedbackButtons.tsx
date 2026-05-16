import { useState } from "react";
import { ThumbsUp, ThumbsDown, MessageSquarePlus, Check, X } from "lucide-react";
import clsx from "clsx";
import { api } from "../api";

/**
 * Tag a completed chat turn with feedback. Three controls:
 *  - 👍 / 👎  → posts feedback: "good" / "bad"
 *  - ✎ correction → expands a textarea so the user can describe what
 *    should have happened. Saved alongside the feedback in the memory
 *    entry's metadata; surfaces in future prompt blocks for similar
 *    queries.
 *
 * The component shows pending / saved / error states so the user knows
 * the click was acknowledged. Pressing the same thumb twice clears the
 * annotation (backend treats feedback=null as "clear").
 */
interface Props {
  taskId: string;
  // Caller can pass initial state when re-rendering an already-annotated
  // turn (e.g. when scrolled back into view). Optional — defaults to
  // unannotated.
  initialFeedback?: "good" | "bad" | null;
  initialCorrection?: string | null;
  // Test seam: replace the api method with a stub.
  onSubmit?: typeof api.memoryFeedback;
}

type Status = "idle" | "saving" | "saved" | "error";

export function FeedbackButtons({
  taskId,
  initialFeedback = null,
  initialCorrection = null,
  onSubmit,
}: Props): JSX.Element {
  const submit = onSubmit ?? api.memoryFeedback;
  const [feedback, setFeedback] = useState<"good" | "bad" | null>(initialFeedback);
  const [correction, setCorrection] = useState<string>(initialCorrection ?? "");
  const [draftCorrection, setDraftCorrection] = useState<string>(initialCorrection ?? "");
  const [expanded, setExpanded] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);

  const post = async (
    body: { feedback?: "good" | "bad" | null; correction?: string | null },
  ): Promise<boolean> => {
    setStatus("saving");
    setError(null);
    try {
      await submit(taskId, body);
      setStatus("saved");
      setTimeout(() => setStatus((s) => (s === "saved" ? "idle" : s)), 1500);
      return true;
    } catch (e) {
      setStatus("error");
      setError((e as Error).message);
      return false;
    }
  };

  const click = async (value: "good" | "bad"): Promise<void> => {
    // Toggle: clicking the active thumb clears the annotation.
    const next = feedback === value ? null : value;
    const ok = await post({ feedback: next });
    if (ok) setFeedback(next);
  };

  const saveCorrection = async (): Promise<void> => {
    const trimmed = draftCorrection.trim();
    const ok = await post({ correction: trimmed || null });
    if (ok) {
      setCorrection(trimmed);
      setExpanded(false);
    }
  };

  const cancelCorrection = (): void => {
    setDraftCorrection(correction);
    setExpanded(false);
  };

  return (
    <div className="feedback-controls mt-1 space-y-1.5">
      <div className="flex items-center gap-1 text-[10px] font-mono text-text-muted">
        <button
          onClick={() => void click("good")}
          aria-label="mark as good"
          aria-pressed={feedback === "good"}
          disabled={status === "saving"}
          className={clsx(
            "feedback-good p-1 rounded transition-colors",
            feedback === "good"
              ? "text-accent-green bg-accent-green/10 border border-accent-green/30"
              : "text-text-muted hover:text-accent-green",
          )}
        >
          <ThumbsUp size={11} strokeWidth={2.25} />
        </button>
        <button
          onClick={() => void click("bad")}
          aria-label="mark as bad"
          aria-pressed={feedback === "bad"}
          disabled={status === "saving"}
          className={clsx(
            "feedback-bad p-1 rounded transition-colors",
            feedback === "bad"
              ? "text-accent-red bg-accent-red/10 border border-accent-red/30"
              : "text-text-muted hover:text-accent-red",
          )}
        >
          <ThumbsDown size={11} strokeWidth={2.25} />
        </button>
        <button
          onClick={() => setExpanded((v) => !v)}
          aria-label="add correction"
          aria-expanded={expanded}
          className={clsx(
            "feedback-correction-toggle p-1 rounded transition-colors flex items-center gap-1",
            correction
              ? "text-accent-blue"
              : "text-text-muted hover:text-accent-blue",
          )}
          title={correction ? `correction saved: ${correction}` : "add a correction"}
        >
          <MessageSquarePlus size={11} strokeWidth={2.25} />
          {correction && <span className="text-[9px]">✓</span>}
        </button>
        {status === "saving" && <span>saving…</span>}
        {status === "saved" && <span className="text-accent-green">saved</span>}
        {status === "error" && (
          <span className="text-accent-red" title={error ?? ""}>
            error
          </span>
        )}
      </div>
      {expanded && (
        <div className="feedback-correction-form flex items-start gap-2">
          <textarea
            value={draftCorrection}
            onChange={(e) => setDraftCorrection(e.target.value)}
            rows={2}
            placeholder="What should have happened instead? (saved on every memory entry for this task)"
            className="flex-1 bg-dark-panel border border-border-subtle rounded-md px-2 py-1 text-[12px] font-mono text-text-primary placeholder-text-muted focus:outline-none focus:border-accent-blue/60 transition-colors resize-y"
          />
          <div className="flex flex-col gap-1">
            <button
              onClick={() => void saveCorrection()}
              disabled={status === "saving" || draftCorrection === correction}
              aria-label="save correction"
              className="p-1 rounded text-accent-green hover:bg-accent-green/10 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Check size={12} strokeWidth={2.25} />
            </button>
            <button
              onClick={cancelCorrection}
              aria-label="cancel correction"
              className="p-1 rounded text-text-muted hover:text-text-secondary"
            >
              <X size={12} strokeWidth={2.25} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
