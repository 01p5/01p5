import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Card, SectionHeader } from "./Card";

describe("Card", () => {
  it("passes className through to the rendered <div>", () => {
    const { container } = render(
      <Card className="mine-extra-class">stuff</Card>,
    );
    const div = container.firstElementChild as HTMLElement;
    expect(div.tagName).toBe("DIV");
    expect(div).toHaveClass("mine-extra-class");
    // Still has the built-in styling.
    expect(div).toHaveClass("bg-dark-panel");
  });

  it("renders children", () => {
    render(<Card>card-body-text</Card>);
    expect(screen.getByText("card-body-text")).toBeInTheDocument();
  });

  it("forwards arbitrary HTML props like id", () => {
    const { container } = render(<Card id="abc" />);
    expect(container.firstElementChild).toHaveAttribute("id", "abc");
  });
});

describe("SectionHeader", () => {
  it("renders the title text in an h2", () => {
    render(<SectionHeader title="MY TITLE" />);
    const h2 = screen.getByRole("heading", { level: 2 });
    expect(h2).toHaveTextContent("MY TITLE");
  });

  it("renders hint text", () => {
    render(<SectionHeader title="x" hint="some hint" />);
    expect(screen.getByText("some hint")).toBeInTheDocument();
  });

  it("renders right slot", () => {
    render(
      <SectionHeader
        title="x"
        right={<button data-testid="rt">right thing</button>}
      />,
    );
    expect(screen.getByTestId("rt")).toBeInTheDocument();
  });

  it("does not render a hint element when hint is missing", () => {
    render(<SectionHeader title="x" />);
    // Only the h2 should be present in the header.
    expect(screen.getByRole("heading", { level: 2 })).toBeInTheDocument();
    expect(screen.queryByText(/some hint/i)).not.toBeInTheDocument();
  });
});
