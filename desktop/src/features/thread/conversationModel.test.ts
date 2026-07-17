import { describe, expect, it } from "vitest";

import type { Item, Turn } from "../../generated/app-server";
import {
  buildConversationTurns,
  formatTurnDuration,
  turnDurationSeconds,
} from "./conversationModel";

const turn: Turn = {
  id: "turn-1",
  threadId: "thread-1",
  ordinal: 1,
  prompt: "Inspect the repository",
  status: "completed",
  stopReason: "completed",
  errorCode: null,
  errorMessage: null,
  startedAt: "2026-07-17T01:00:00Z",
  completedAt: "2026-07-17T01:01:05Z",
};

function item(
  id: string,
  ordinal: number,
  kind: Item["kind"],
  summary: string,
): Item {
  return {
    id,
    threadId: turn.threadId,
    turnId: turn.id,
    ordinal,
    kind,
    status: "completed",
    summary,
    payload: { text: summary },
    createdAt: "2026-07-17T01:00:01Z",
    updatedAt: "2026-07-17T01:00:02Z",
  };
}

describe("conversationModel", () => {
  it("groups one Turn into prompt, execution ledger, final answer, and completion", () => {
    const items = [
      item("assistant", 4, "assistant_message", "Final answer"),
      item("tool", 3, "command_execution", "Ran tests"),
      item("user", 1, "user_message", "Inspect the repository"),
      item("completion", 5, "completion", "Turn complete"),
      item("plan", 2, "plan", "Execution plan"),
    ];

    const [group] = buildConversationTurns([turn], items);

    expect(group.userMessages.map((candidate) => candidate.id)).toEqual([
      "user",
    ]);
    expect(group.executionItems.map((candidate) => candidate.id)).toEqual([
      "plan",
      "tool",
    ]);
    expect(group.assistantMessages.map((candidate) => candidate.id)).toEqual([
      "assistant",
    ]);
    expect(group.completion?.id).toBe("completion");
  });

  it("keeps recovered items whose Turn record is unavailable", () => {
    const orphan = {
      ...item("orphan", 1, "assistant_message", "Recovered answer"),
      turnId: "missing-turn",
    };

    const groups = buildConversationTurns([], [orphan]);

    expect(groups).toHaveLength(1);
    expect(groups[0].turn).toBeNull();
    expect(groups[0].assistantMessages[0].id).toBe("orphan");
  });

  it("formats stable elapsed durations for completed Turns", () => {
    expect(turnDurationSeconds(turn)).toBe(65);
    expect(formatTurnDuration(65)).toBe("1m 05s");
    expect(formatTurnDuration(3661)).toBe("1h 01m");
  });
});
