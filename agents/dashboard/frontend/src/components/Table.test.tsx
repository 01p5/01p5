import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Table, type Column } from "./Table";

interface Row {
  id: string;
  name: string;
  count: number;
}

const cols: Column<Row>[] = [
  { key: "name", header: "Name", accessor: (r) => r.name },
  { key: "count", header: "Count", accessor: (r) => r.count, align: "right" },
];

const rows: Row[] = [
  { id: "a", name: "alpha", count: 10 },
  { id: "b", name: "bravo", count: 20 },
];

describe("Table", () => {
  it("renders empty state when rows=[]", () => {
    render(<Table columns={cols} rows={[]} rowKey={(r) => r.id} />);
    expect(screen.getByText("no entries")).toBeInTheDocument();
  });

  it("renders a custom empty state", () => {
    render(
      <Table columns={cols} rows={[]} rowKey={(r) => r.id} empty="nothing here" />,
    );
    expect(screen.getByText("nothing here")).toBeInTheDocument();
  });

  it("renders headers and cells using accessor", () => {
    render(<Table columns={cols} rows={rows} rowKey={(r) => r.id} />);
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Count" })).toBeInTheDocument();
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("bravo")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
  });

  it("renders custom cell renderer when provided", () => {
    const customCols: Column<Row>[] = [
      {
        key: "name",
        header: "X",
        cell: (r) => <em data-testid={`em-${r.id}`}>{r.name}!</em>,
      },
    ];
    render(<Table columns={customCols} rows={rows} rowKey={(r) => r.id} />);
    expect(screen.getByTestId("em-a")).toHaveTextContent("alpha!");
  });

  it("applies text-right class when column align=right (on header + cell)", () => {
    render(<Table columns={cols} rows={rows} rowKey={(r) => r.id} />);
    expect(screen.getByRole("columnheader", { name: "Count" })).toHaveClass("text-right");
    expect(screen.getByText("10")).toHaveClass("text-right");
  });

  it("fires onRowClick when row clicked", async () => {
    const handler = vi.fn();
    render(
      <Table columns={cols} rows={rows} rowKey={(r) => r.id} onRowClick={handler} />,
    );
    await userEvent.click(screen.getByText("alpha"));
    expect(handler).toHaveBeenCalledWith(rows[0]);
  });
});
