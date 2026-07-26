import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Item, Turn } from "../../generated/app-server";
import { buildConversationTurns } from "./conversationModel";
import { TurnBlock } from "./TurnBlock";

const turn: Turn = {
  id: "turn-1",
  threadId: "thread-1",
  ordinal: 1,
  prompt: "Inspect and repair",
  status: "completed",
  stopReason: "completed",
  errorCode: null,
  errorMessage: null,
  startedAt: "2026-07-23T01:00:00Z",
  completedAt: "2026-07-23T01:00:10Z",
};

function item(
  id: string,
  ordinal: number,
  kind: Item["kind"],
  summary: string,
  payload: Item["payload"],
): Item {
  return {
    id,
    threadId: turn.threadId,
    turnId: turn.id,
    ordinal,
    kind,
    status: "completed",
    summary,
    payload,
    createdAt: "2026-07-23T01:00:01Z",
    updatedAt: "2026-07-23T01:00:02Z",
  };
}

describe("TurnBlock", () => {
  afterEach(cleanup);

  it("renders commentary, tools, and the final answer in ordinal order", () => {
    const items = [
      item("user", 1, "user_message", turn.prompt, { text: turn.prompt }),
      item("commentary", 2, "assistant_message", "Progress note", {
        text: "Progress note",
        phase: "commentary",
      }),
      item("read", 3, "command_execution", "Read configuration", {
        activity: {
          kind: "read",
          label: "Read",
          subject: "deepcode.toml",
        },
      }),
      item("final", 4, "assistant_message", "Repair complete", {
        text: "Repair complete",
        phase: "final_answer",
      }),
      item("completion", 5, "completion", "Turn complete", {
        stopReason: "completed",
      }),
    ];
    const [group] = buildConversationTurns([turn], items);

    render(
      <TurnBlock
        group={group}
        approvalsByItem={new Map()}
        selectedItemId={null}
        busy={false}
        onSelectItem={vi.fn()}
        onOpenInspector={vi.fn()}
        onRespondToApproval={vi.fn()}
        onRetryTurn={vi.fn()}
        onCancelQueuedTurn={vi.fn()}
      />,
    );

    const commentary = screen.getByText("Progress note");
    const activity = screen.getByText("deepcode.toml");
    const finalAnswer = screen.getByText("Repair complete");
    expect(
      commentary.compareDocumentPosition(activity) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      activity.compareDocumentPosition(finalAnswer) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
