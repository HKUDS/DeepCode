import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Item } from "../../generated/app-server";
import { ReasoningBlock } from "./ReasoningBlock";

function reasoningItem(
  status: Item["status"],
  payload: Item["payload"],
): Item {
  return {
    id: "reasoning-1",
    threadId: "thread-1",
    turnId: "turn-1",
    ordinal: 2,
    kind: "reasoning_summary",
    status,
    summary: "Thinking",
    payload,
    createdAt: "2026-07-29T00:00:00Z",
    updatedAt: "2026-07-29T00:00:02Z",
  };
}

describe("ReasoningBlock", () => {
  afterEach(cleanup);

  it("auto-opens while streaming and auto-collapses after completion", () => {
    const item = reasoningItem("in_progress", {
      schemaVersion: 1,
      summaryText: "Checking the repository.",
      traceText: "",
      availability: "available",
      effort: "high",
      streaming: true,
    });
    const { container, rerender } = render(
      <ReasoningBlock item={item} mode="normal" />,
    );

    const details = container.querySelector("details");
    expect(details?.open).toBe(true);
    expect(screen.getByText(/Thinking · high/i)).toBeTruthy();

    rerender(
      <ReasoningBlock
        item={{
          ...item,
          status: "completed",
          payload: { ...item.payload, durationMs: 2100, streaming: false },
        }}
        mode="normal"
      />,
    );

    expect(details?.open).toBe(false);
    expect(screen.getByText("Thought for 2s")).toBeTruthy();
  });

  it("keeps provider trace behind a second disclosure in normal mode", () => {
    const { container } = render(
      <ReasoningBlock
        item={reasoningItem("completed", {
          schemaVersion: 1,
          summaryText: "Checked the constraints.",
          traceText: "Provider trace detail.",
          availability: "available",
          durationMs: 1000,
          streaming: false,
        })}
        mode="normal"
      />,
    );

    const outer = container.querySelector("details");
    expect(outer?.open).toBe(false);
    fireEvent.click(screen.getByText("Thought for 1s"));
    expect(outer?.open).toBe(true);
    expect(
      screen.getByText("Provider reasoning details").closest("details")?.open,
    ).toBe(false);
  });

  it("opens the full provider trace in verbose mode", () => {
    const { container } = render(
      <ReasoningBlock
        item={reasoningItem("completed", {
          schemaVersion: 1,
          summaryText: "Checked the constraints.",
          traceText: "Provider trace detail.",
          availability: "available",
          durationMs: 1000,
          streaming: false,
        })}
        mode="verbose"
      />,
    );

    expect(container.querySelector("details")?.open).toBe(true);
    expect(screen.getByRole("heading", { level: 4 }).textContent).toBe(
      "Provider reasoning details",
    );
    expect(screen.getByText("Provider trace detail.")).toBeTruthy();
  });

  it("does not let an automatic mode change erase a manual disclosure choice", () => {
    const item = reasoningItem("completed", {
      schemaVersion: 1,
      summaryText: "Checked the constraints.",
      traceText: "Provider trace detail.",
      availability: "available",
      durationMs: 1000,
      streaming: false,
    });
    const { container, rerender } = render(
      <ReasoningBlock item={item} mode="verbose" />,
    );
    const details = container.querySelector("details");

    fireEvent.click(screen.getByText("Thought for 1s"));
    expect(details?.open).toBe(false);
    rerender(<ReasoningBlock item={item} mode="normal" />);
    expect(details?.open).toBe(false);
    rerender(<ReasoningBlock item={item} mode="verbose" />);
    expect(details?.open).toBe(false);
  });

  it("explains opaque provider reasoning instead of fabricating text", () => {
    render(
      <ReasoningBlock
        item={reasoningItem("completed", {
          schemaVersion: 1,
          summaryText: "",
          traceText: "",
          availability: "opaque",
          durationMs: 500,
          streaming: false,
        })}
        mode="verbose"
      />,
    );

    expect(
      screen.getByText(
        "This model completed reasoning without returning displayable details.",
      ),
    ).toBeTruthy();
  });

  it("renders legacy Session summary payloads", () => {
    render(
      <ReasoningBlock
        item={reasoningItem("completed", { text: "Legacy reasoning summary." })}
        mode="verbose"
      />,
    );

    expect(screen.getByText("Legacy reasoning summary.")).toBeTruthy();
  });
});
