import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FeedbackButtons } from "./FeedbackButtons";

const NOOP_SUBMIT = vi.fn().mockResolvedValue({ updated: true, task_id: "T1" });

beforeEach(() => {
  NOOP_SUBMIT.mockClear();
});

describe("FeedbackButtons", () => {
  it("renders three controls: good / bad / correction toggle", () => {
    render(<FeedbackButtons taskId="T1" onSubmit={NOOP_SUBMIT} />);
    expect(screen.getByRole("button", { name: /mark as good/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /mark as bad/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add correction/i })).toBeInTheDocument();
  });

  it("clicking 👍 posts feedback=good", async () => {
    render(<FeedbackButtons taskId="T1" onSubmit={NOOP_SUBMIT} />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /mark as good/i }));
    });
    expect(NOOP_SUBMIT).toHaveBeenCalledWith("T1", { feedback: "good" });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /mark as good/i })).toHaveAttribute(
        "aria-pressed", "true",
      ),
    );
  });

  it("clicking 👎 posts feedback=bad", async () => {
    render(<FeedbackButtons taskId="T1" onSubmit={NOOP_SUBMIT} />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /mark as bad/i }));
    });
    expect(NOOP_SUBMIT).toHaveBeenCalledWith("T1", { feedback: "bad" });
  });

  it("clicking the active thumb again posts feedback=null (clear)", async () => {
    render(
      <FeedbackButtons
        taskId="T1"
        initialFeedback="good"
        onSubmit={NOOP_SUBMIT}
      />,
    );
    expect(screen.getByRole("button", { name: /mark as good/i })).toHaveAttribute(
      "aria-pressed", "true",
    );
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /mark as good/i }));
    });
    expect(NOOP_SUBMIT).toHaveBeenCalledWith("T1", { feedback: null });
  });

  it("expands the correction form when the third button is clicked", async () => {
    render(<FeedbackButtons taskId="T1" onSubmit={NOOP_SUBMIT} />);
    expect(screen.queryByRole("textbox")).toBeNull();
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add correction/i }));
    });
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("saving a correction posts the trimmed string", async () => {
    render(<FeedbackButtons taskId="T1" onSubmit={NOOP_SUBMIT} />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add correction/i }));
    });
    const textbox = screen.getByRole("textbox");
    await act(async () => {
      await userEvent.type(textbox, "use --namespace=staging");
    });
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /save correction/i }));
    });
    expect(NOOP_SUBMIT).toHaveBeenCalledWith("T1", {
      correction: "use --namespace=staging",
    });
    // Form collapses after save.
    await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());
  });

  it("save is disabled while the draft equals the saved correction", async () => {
    render(
      <FeedbackButtons
        taskId="T1"
        initialCorrection="existing note"
        onSubmit={NOOP_SUBMIT}
      />,
    );
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add correction/i }));
    });
    const save = screen.getByRole("button", { name: /save correction/i });
    expect(save).toBeDisabled();
  });

  it("cancel closes the form without posting", async () => {
    render(<FeedbackButtons taskId="T1" onSubmit={NOOP_SUBMIT} />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add correction/i }));
    });
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /cancel correction/i }));
    });
    expect(NOOP_SUBMIT).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("shows 'error' label when the submit fails", async () => {
    const fail = vi.fn().mockRejectedValue(new Error("403 Forbidden"));
    render(<FeedbackButtons taskId="T1" onSubmit={fail} />);
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /mark as good/i }));
    });
    await waitFor(() => expect(screen.getByText("error")).toBeInTheDocument());
    expect(screen.getByText("error").getAttribute("title")).toContain("403 Forbidden");
  });

  it("clears the correction by saving an empty trimmed draft (sends null)", async () => {
    render(
      <FeedbackButtons
        taskId="T1"
        initialCorrection="kill this"
        onSubmit={NOOP_SUBMIT}
      />,
    );
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /add correction/i }));
    });
    const textbox = screen.getByRole("textbox");
    await act(async () => {
      await userEvent.clear(textbox);
      await userEvent.type(textbox, "   ");  // whitespace-only
    });
    await act(async () => {
      await userEvent.click(screen.getByRole("button", { name: /save correction/i }));
    });
    expect(NOOP_SUBMIT).toHaveBeenCalledWith("T1", { correction: null });
  });
});
