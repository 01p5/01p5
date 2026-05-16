import clsx from "clsx";

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  /** Renderer for cell content. Default: stringify the value at `accessor`. */
  cell?: (row: T) => React.ReactNode;
  accessor?: (row: T) => unknown;
  className?: string;
  /** Header alignment helper. */
  align?: "left" | "right" | "center";
}

interface TableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  empty?: React.ReactNode;
  onRowClick?: (row: T) => void;
  className?: string;
}

export function Table<T>({
  columns, rows, rowKey, empty, onRowClick, className,
}: TableProps<T>): JSX.Element {
  if (rows.length === 0) {
    return (
      <div className="text-text-muted text-sm italic px-3 py-6 text-center">
        {empty ?? "no entries"}
      </div>
    );
  }
  return (
    <div className={clsx("overflow-auto", className)}>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] uppercase tracking-[1.5px] text-text-muted">
            {columns.map((c) => (
              <th
                key={c.key}
                className={clsx(
                  "text-left font-medium px-3 py-2 border-b border-border-subtle",
                  c.align === "right" && "text-right",
                  c.align === "center" && "text-center",
                  c.className,
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={clsx(
                "border-b border-border-subtle/60 hover:bg-dark-tertiary/40 transition-colors",
                onRowClick && "cursor-pointer",
              )}
            >
              {columns.map((c) => {
                const content = c.cell
                  ? c.cell(row)
                  : c.accessor
                    ? String(c.accessor(row) ?? "")
                    : "";
                return (
                  <td
                    key={c.key}
                    className={clsx(
                      "px-3 py-2 text-text-primary",
                      c.align === "right" && "text-right",
                      c.align === "center" && "text-center",
                      c.className,
                    )}
                  >
                    {content}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
