import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Item, Turn } from "../../generated/app-server";
import { buildConversationTurns } from "./conversationModel";
import type { TranscriptMode } from "./transcriptMode";
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

function renderTurn(items: Item[], transcriptMode: TranscriptMode = "normal") {
  const [group] = buildConversationTurns([turn], items);
  return render(
    <TurnBlock
      group={group}
      approvalsByItem={new Map()}
      selectedItemId={null}
      transcriptMode={transcriptMode}
      busy={false}
      onSelectItem={vi.fn()}
      onOpenInspector={vi.fn()}
      onRespondToApproval={vi.fn()}
      onRetryTurn={vi.fn()}
      onCancelQueuedTurn={vi.fn()}
    />,
  );
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
    renderTurn(items);

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

  it("shows unsaved updates as a warning, not as a failed step", () => {
    const notice = "Some updates in this turn could not be saved.";
    const { container } = renderTurn([
      item("user", 1, "user_message", turn.prompt, { text: turn.prompt }),
      item("final", 2, "assistant_message", "Repair complete", {
        text: "Repair complete",
        phase: "final_answer",
      }),
      item("notice", 3, "error", notice, {
        code: "UPDATES_NOT_SAVED",
        message: notice,
        severity: "warning",
      }),
      item("completion", 4, "completion", "Turn complete", {
        stopReason: "completed",
      }),
    ]);

    const row = container.querySelector('[data-warning="true"]');
    expect(row?.textContent).toContain(notice);
    expect(row?.textContent).toContain("Warning");
    expect(screen.queryByText("Execution error")).toBeNull();
    expect(row?.textContent).not.toMatch(/completed|failed/);
  });

  it("shows a Goal continuation as a note with its instruction folded away", () => {
    const instruction = "Continue working toward the active Thread Goal.";
    const steer = "Also keep the public API unchanged.";
    renderTurn([
      item("goal", 1, "user_message", instruction, {
        text: instruction,
        source: "goal_continuation",
      }),
      item("steer", 2, "user_message", steer, { text: steer, source: "steer" }),
    ]);

    const note = screen.getByText("Goal continued automatically").closest("details");
    expect(note).not.toBeNull();
    expect(note?.open).toBe(false);
    expect(note?.textContent).toContain(instruction);
    // What the user typed stays a message of their own.
    expect(screen.getByText(steer).closest("details")).toBeNull();
  });

  it("unfolds the Goal instruction in the verbose transcript", () => {
    const instruction = "Continue working toward the active Thread Goal.";
    renderTurn(
      [
        item("goal", 1, "user_message", instruction, {
          text: instruction,
          source: "goal_continuation",
        }),
      ],
      "verbose",
    );
    expect(
      screen.getByText("Goal continued automatically").closest("details")?.open,
    ).toBe(true);
  });
});
