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
  payload: Item["payload"] = { text: summary },
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
    createdAt: "2026-07-17T01:00:01Z",
    updatedAt: "2026-07-17T01:00:02Z",
  };
}

describe("conversationModel", () => {
  it("keeps assistant messages and execution items in true ordinal order", () => {
    const items = [
      item("assistant-final", 6, "assistant_message", "Final answer"),
      item("tool", 3, "command_execution", "Ran tests"),
      item("user", 1, "user_message", "Inspect the repository"),
      item("completion", 7, "completion", "Turn complete"),
      item("plan", 2, "plan", "Execution plan"),
      item("assistant-commentary", 4, "assistant_message", "Tests failed"),
      item("repair", 5, "file_change", "Repaired implementation"),
    ];

    const [group] = buildConversationTurns([turn], items);

    expect(group.userMessages.map((candidate) => candidate.id)).toEqual([
      "user",
    ]);
    expect(
      group.timeline.map((entry) =>
        entry.type === "item" ? entry.item.id : entry.id,
      ),
    ).toEqual([
      "plan",
      "tool",
      "assistant-commentary",
      "repair",
      "assistant-final",
    ]);
    expect(group.completion?.id).toBe("completion");
  });

  it("folds only adjacent read, search, and list activities", () => {
    const activity = (kind: string, subject: string) => ({
      activity: { kind, label: kind, subject },
      text: subject,
    });
    const items = [
      item("user", 1, "user_message", "Inspect"),
      item("read", 2, "command_execution", "Read README", activity("read", "README.md")),
      item("search", 3, "command_execution", "Searched symbols", activity("search", "symbols")),
      item("commentary", 4, "assistant_message", "I found the entrypoint"),
      item("list", 5, "command_execution", "Listed files", activity("list", "src")),
      item("run", 6, "command_execution", "Ran tests", activity("run", "pytest")),
    ];

    const [group] = buildConversationTurns([turn], items);

    expect(group.timeline).toHaveLength(4);
    expect(group.timeline[0]).toMatchObject({
      type: "activity_group",
      id: "exploration:read",
      items: [{ id: "read" }, { id: "search" }],
    });
    expect(group.timeline[1]).toMatchObject({
      type: "item",
      item: { id: "commentary" },
    });
    expect(group.timeline[2]).toMatchObject({
      type: "item",
      item: { id: "list" },
    });
    expect(group.timeline[3]).toMatchObject({
      type: "item",
      item: { id: "run" },
    });
  });

  it("leaves legacy activity items readable when semantic metadata is absent", () => {
    const items = [
      item("legacy-read", 1, "command_execution", "Read README"),
      item("legacy-search", 2, "command_execution", "Searched symbols"),
    ];

    const [group] = buildConversationTurns([turn], items);

    expect(group.timeline.map((entry) => entry.type)).toEqual(["item", "item"]);
  });

  it("keeps recovered items whose Turn record is unavailable", () => {
    const orphan = {
      ...item("orphan", 1, "assistant_message", "Recovered answer"),
      turnId: "missing-turn",
    };

    const groups = buildConversationTurns([], [orphan]);

    expect(groups).toHaveLength(1);
    expect(groups[0].turn).toBeNull();
    expect(groups[0].timeline[0]).toMatchObject({
      type: "item",
      item: { id: "orphan" },
    });
  });

  it("formats stable elapsed durations for completed Turns", () => {
    expect(turnDurationSeconds(turn)).toBe(65);
    expect(formatTurnDuration(65)).toBe("1m 05s");
    expect(formatTurnDuration(3661)).toBe("1h 01m");
  });
});
