import { cloneElement, isValidElement } from "react";
import clsx from "clsx";
import { Loader2 } from "lucide-react";

type Variant = "primary" | "secondary" | "danger" | "ghost";
type Size = "sm" | "md";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  icon?: React.ReactNode;
}

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent-green text-dark-primary hover:bg-accent-green-dim border border-accent-green hover:border-accent-green-dim font-semibold",
  secondary:
    "bg-dark-control text-text-primary hover:bg-border-active border border-border-subtle",
  danger:
    "bg-accent-red/15 text-accent-red hover:bg-accent-red/25 border border-accent-red/30",
  ghost:
    "bg-transparent text-text-secondary hover:text-text-primary hover:bg-dark-control border border-transparent",
};

const SIZES: Record<Size, string> = {
  sm: "text-xs px-2.5 py-1 gap-1.5",
  md: "text-sm px-3.5 py-1.5 gap-1.5",
};

// Default icon size + stroke per button size. 12px lucide icons at
// stroke-width 2 visibly collapse on dark backgrounds — bumping to
// 14/16 with strokeWidth 2.25 keeps them readable while staying tight.
const ICON_DEFAULTS: Record<Size, { size: number; strokeWidth: number }> = {
  sm: { size: 16, strokeWidth: 2.25 },
  md: { size: 18, strokeWidth: 2.25 },
};

function normalizeIcon(node: React.ReactNode, size: Size): React.ReactNode {
  if (!isValidElement(node)) return node;
  const defaults = ICON_DEFAULTS[size];
  // Always override caller-passed size/strokeWidth — the whole point
  // of routing through Button is that the button decides the icon
  // metrics, not the individual page. 12px lucide strokes collapse
  // visually on dark backgrounds; defaults below stay readable.
  return cloneElement(node as React.ReactElement<Record<string, unknown>>, {
    size: defaults.size,
    strokeWidth: defaults.strokeWidth,
  });
}

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  icon,
  children,
  className,
  disabled,
  ...rest
}: ButtonProps): JSX.Element {
  const defaults = ICON_DEFAULTS[size];
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={clsx(
        "inline-flex items-center justify-center rounded-md transition-colors duration-150",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
    >
      {loading
        ? <Loader2 size={defaults.size} strokeWidth={defaults.strokeWidth} className="animate-spin" />
        : normalizeIcon(icon, size)}
      {children}
    </button>
  );
}
