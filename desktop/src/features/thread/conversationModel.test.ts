import { describe, expect, it } from "vitest";

import type { Item, Turn } from "../../generated/app-server";
import {
  buildConversationTurns,
  formatTurnDuration,
  historyBatchStart,
  summarizeWork,
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

  const activity = (kind: string, subject: string) => ({
    activity: { kind, label: kind, subject },
    text: subject,
  });
  const entryIds = (items: Item[]) =>
    buildConversationTurns([turn], items)[0].timeline.map((entry) =>
      entry.type === "item" ? entry.item.id : [entry.id, entry.items.map((step) => step.id)],
    );

  it("folds the work between what the user sees into one group", () => {
    expect(
      entryIds([
        item("user", 1, "user_message", "Inspect"),
        item("think", 2, "reasoning_summary", "Thinking"),
        item("read", 3, "command_execution", "Read README", activity("read", "README.md")),
        item("run", 4, "command_execution", "Ran tests", activity("run", "pytest")),
        item("commentary", 5, "assistant_message", "Tests pass; checking the docs"),
        item("edit", 6, "file_change", "Edited docs"),
        item("diff", 7, "diff", "Docs diff"),
        item("final", 8, "assistant_message", "Done"),
      ]),
    ).toEqual([
      ["work:think", ["think", "read", "run"]],
      "commentary",
      ["work:edit", ["edit", "diff"]],
      "final",
    ]);
  });

  it("leaves a single action, and thinking alone, as they are", () => {
    expect(
      entryIds([
        item("think", 1, "reasoning_summary", "Thinking"),
        item("run", 2, "command_execution", "Ran tests"),
        item("commentary", 3, "assistant_message", "Looking further"),
        item("think-2", 4, "reasoning_summary", "Thinking"),
        item("think-3", 5, "reasoning_summary", "Thinking"),
      ]),
    ).toEqual(["think", "run", "commentary", "think-2", "think-3"]);
  });

  it("never folds approvals, errors, plans or test results into a group", () => {
    expect(
      entryIds([
        item("run", 1, "command_execution", "Ran tests"),
        item("approval", 2, "approval_request", "Approve the edit"),
        item("edit", 3, "file_change", "Edited a file"),
        item("plan", 4, "plan", "Plan"),
        item("run-2", 5, "command_execution", "Ran tests"),
        item("error", 6, "error", "Command failed"),
        item("run-3", 7, "command_execution", "Ran tests"),
        item("tests", 8, "test_result", "3 passed"),
        item("run-4", 9, "command_execution", "Ran tests"),
      ]),
    ).toEqual([
      "run",
      "approval",
      "edit",
      "plan",
      "run-2",
      "error",
      "run-3",
      "tests",
      "run-4",
    ]);
  });

  it("tallies what a group did, what failed, and what runs now", () => {
    const steps = [
      { ...item("think", 1, "reasoning_summary", "Thinking"), createdAt: "2026-07-17T01:00:00Z" },
      item("read", 2, "command_execution", "Read", activity("read", "a.py")),
      item("grep", 3, "tool_call", "Search", activity("search", "TODO")),
      item("glob", 4, "tool_call", "List", activity("list", "src")),
      { ...item("run", 5, "command_execution", "Ran tests", activity("run", "pytest")), status: "failed" as const },
      item("legacy", 6, "command_execution", "Ran something"),
      item("mcp", 7, "tool_call", "Browser click", activity("other", "button")),
      item("edit", 8, "file_change", "Edited a.py"),
      // A stopped Turn fails its open thinking; that is no failed action.
      { ...item("stopped", 9, "reasoning_summary", "Thinking"), status: "failed" as const },
      { ...item("bad-time", 10, "file_change", "Edited b.py"), createdAt: "not a time" },
      {
        ...item("live", 11, "command_execution", "Running", activity("run", "npm test")),
        status: "in_progress" as const,
        updatedAt: "2026-07-17T01:02:05Z",
      },
    ];

    const summary = summarizeWork(steps);

    expect(summary.tallies).toEqual([
      ["commands", 3],
      ["edits", 2],
      ["reads", 1],
      ["searches", 2],
      ["tools", 1],
    ]);
    expect(summary.failed).toBe(1);
    expect(summary.current?.id).toBe("live");
    expect(summary.seconds).toBe(125);
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

  it("batches history from the end by rows shown, always at least one Turn", () => {
    // Turns of 4, 4, 11 and 4 rows: the prompt, and each assistant message.
    const sizes = [3, 3, 10, 3];
    const turns = sizes.map((_, index) => ({ ...turn, id: `turn-${index}`, ordinal: index + 1 }));
    const items = sizes.flatMap((size, index) =>
      Array.from({ length: size }, (_, step) => ({
        ...item(`t${index}-${step}`, step + 1, "assistant_message", "text"),
        turnId: `turn-${index}`,
      })),
    );
    const groups = buildConversationTurns(turns, items);

    expect(historyBatchStart(groups, 4, 100)).toBe(0);
    expect(historyBatchStart(groups, 4, 15)).toBe(2);
    expect(historyBatchStart(groups, 4, 14)).toBe(3);
    // A Turn larger than the budget still comes whole.
    expect(historyBatchStart(groups, 3, 5)).toBe(2);
    expect(historyBatchStart(groups, 2, 8)).toBe(0);

    // A folded run of hundreds of steps is a single row: two such Turns,
    // with their prompts, fit in four rows.
    const folded = [0, 1].map((index) => ({ ...turn, id: `long-${index}`, ordinal: index + 1 }));
    const steps = folded.flatMap((each) =>
      Array.from({ length: 300 }, (_, step) => ({
        ...item(`${each.id}-${step}`, step + 1, "command_execution", `step ${step}`),
        turnId: each.id,
      })),
    );
    expect(historyBatchStart(buildConversationTurns(folded, steps), 2, 4)).toBe(0);
  });

  it("formats stable elapsed durations for completed Turns", () => {
    expect(turnDurationSeconds(turn)).toBe(65);
    expect(formatTurnDuration(65)).toBe("1m 05s");
    expect(formatTurnDuration(3661)).toBe("1h 01m");
  });
});
