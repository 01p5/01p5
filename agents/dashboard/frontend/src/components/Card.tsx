import clsx from "clsx";

export function Card({
  className,
  ...rest
}: React.HTMLAttributes<HTMLDivElement>): JSX.Element {
  return (
    <div
      {...rest}
      className={clsx(
        "bg-dark-panel border border-border-subtle rounded-md",
        className,
      )}
    />
  );
}

interface SectionHeaderProps {
  title: string;
  hint?: React.ReactNode;
  right?: React.ReactNode;
}
export function SectionHeader({ title, hint, right }: SectionHeaderProps): JSX.Element {
  return (
    <div className="flex items-baseline justify-between mb-3">
      <div className="flex items-baseline gap-2">
        <h2 className="font-display text-xs font-semibold uppercase tracking-[1.5px] text-text-secondary">
          {title}
        </h2>
        {hint && <span className="text-[11px] text-text-muted">{hint}</span>}
      </div>
      {right}
    </div>
  );
}
