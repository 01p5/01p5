import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge, k8sStatusTone } from "./Badge";

describe("Badge", () => {
  it.each([
    ["green", "text-accent-green"],
    ["yellow", "text-accent-yellow"],
    ["red", "text-accent-red"],
    ["blue", "text-accent-blue"],
    ["orange", "text-accent-orange"],
    ["neutral", "text-text-secondary"],
  ] as const)("renders tone=%s with %s class", (tone, expectedClass) => {
    render(<Badge tone={tone}>hi</Badge>);
    expect(screen.getByText("hi")).toHaveClass(expectedClass);
  });

  it("defaults to neutral tone when none provided", () => {
    render(<Badge>x</Badge>);
    expect(screen.getByText("x")).toHaveClass("text-text-secondary");
  });
});

describe("k8sStatusTone", () => {
  it.each([
    ["Running", "green"],
    ["Ready", "green"],
    ["Succeeded", "green"],
    ["Pending", "yellow"],
    ["ContainerCreating", "yellow"],
    ["Failed", "red"],
    ["CrashLoopBackOff", "red"],
    ["Error", "red"],
    ["NotReady", "red"],
    ["Terminating", "orange"],
    ["Unknown", "neutral"],
    ["", "neutral"],
    ["weirdstate", "neutral"],
  ])("maps %s → %s", (input, expected) => {
    expect(k8sStatusTone(input)).toBe(expected);
  });
});
