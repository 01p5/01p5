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
  sm: "text-xs px-2.5 py-1 gap-1",
  md: "text-sm px-3.5 py-1.5 gap-1.5",
};

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
      {loading ? <Loader2 size={size === "sm" ? 12 : 14} className="animate-spin" /> : icon}
      {children}
    </button>
  );
}
