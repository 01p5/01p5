import { useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import clsx from "clsx";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  wide?: boolean;
  children: React.ReactNode;
  /** Optional content shown right-aligned in the header (e.g. a primary action button) */
  headerAction?: React.ReactNode;
}

export function Modal({ open, onClose, title, wide, children, headerAction }: ModalProps): JSX.Element | null {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-6"
      onClick={onClose}
    >
      <div
        className={clsx(
          "bg-dark-panel border border-border-subtle rounded-lg shadow-2xl",
          "flex flex-col max-h-[88vh] w-full",
          wide ? "max-w-6xl" : "max-w-4xl",
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between px-5 py-3 border-b border-border-subtle">
          <h2 className="font-display font-semibold text-text-primary text-base">{title}</h2>
          <div className="flex items-center gap-3">
            {headerAction}
            <button
              onClick={onClose}
              className="text-text-muted hover:text-text-primary transition-colors"
              aria-label="Close"
            >
              <X size={18} />
            </button>
          </div>
        </header>
        <div className="flex-1 overflow-auto p-5">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
