import { describe, expect, it } from "vitest";

import type { Goal, Turn } from "../../generated/app-server";
import { deriveGoalPresentation } from "./goalPresentation";

function goal(status: Goal["status"]): Goal {
  return {
    id: "goal-1",
    threadId: "thread-1",
    objective: "Ship",
    status,
    tokenBudget: null,
    tokensUsed: 0,
    timeUsedSeconds: 0,
    skillIds: [],
    createdAt: "2026-07-28T00:00:00Z",
    updatedAt: "2026-07-28T00:00:00Z",
  };
}

function turn(
  id: string,
  status: Turn["status"],
  goalId: string | null,
): Turn {
  return {
    id,
    threadId: "thread-1",
    ordinal: 1,
    prompt: "work",
    goalId,
    status,
    stopReason: null,
    errorCode: null,
    errorMessage: null,
    startedAt: status === "queued" ? null : "2026-07-28T00:00:00Z",
    completedAt:
      status === "completed" ? "2026-07-28T00:01:00Z" : null,
  };
}

describe("deriveGoalPresentation", () => {
  it("derives working only from a live Turn associated with the Goal", () => {
    expect(
      deriveGoalPresentation(goal("active"), [
        turn("turn-1", "running", "goal-1"),
      ]).status,
    ).toBe("working");
  });

  it("derives readyToContinue for an active idle Goal", () => {
    expect(
      deriveGoalPresentation(goal("active"), [
        turn("turn-1", "completed", "goal-1"),
      ]).status,
    ).toBe("readyToContinue");
  });

  it("derives finishing while the deciding Turn is still live", () => {
    expect(
      deriveGoalPresentation(
        goal("complete"),
        [turn("turn-1", "running", "goal-1")],
        "turn-1",
      ).status,
    ).toBe("finishing");
  });

  it("leaves persisted paused and blocked states unchanged", () => {
    expect(deriveGoalPresentation(goal("paused"), []).status).toBe("paused");
    expect(deriveGoalPresentation(goal("blocked"), []).status).toBe("blocked");
  });
});
