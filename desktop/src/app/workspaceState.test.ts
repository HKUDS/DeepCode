import { describe, expect, it } from "vitest";

import type {
  Event,
  Goal,
  GoalOutcome,
  Item,
  JsonValue,
  Thread,
  Turn,
  WorkflowRun,
} from "../generated/app-server";
import {
  initialWorkspaceState,
  workspaceReducer,
} from "./workspaceState";

const turn: Turn = {
  id: "turn-1",
  threadId: "thread-1",
  ordinal: 1,
  prompt: "test",
  status: "running",
  stopReason: null,
  errorCode: null,
  errorMessage: null,
  startedAt: "2026-07-16T00:00:00Z",
  completedAt: null,
};

function item(status: Item["status"], text: string): Item {
  return {
    id: "item-1",
    threadId: "thread-1",
    turnId: turn.id,
    ordinal: 1,
    kind: "assistant_message",
    status,
    summary: text,
    payload: { text },
    createdAt: "2026-07-16T00:00:00Z",
    updatedAt: "2026-07-16T00:00:00Z",
  };
}

function event(sequence: number, value: Item): Event {
  return {
    eventId: `event-${sequence}`,
    sequence,
    type: "item.updated",
    threadId: "thread-1",
    turnId: turn.id,
    itemId: value.id,
    timestamp: "2026-07-16T00:00:00Z",
    payload: { item: value as unknown as JsonValue },
  };
}

describe("workspace event projection", () => {
  const thread: Thread = {
    id: "thread-1",
    projectId: "project-1",
    parentThreadId: null,
    title: "Session",
    mode: "code",
    status: "idle",
    model: null,
    connectionId: null,
    reasoningEffort: null,
    contextWindow: null,
    accessPresetOverride: null,
    workspacePath: "/workspace/project-1",
    worktreePath: null,
    createdAt: "2026-07-16T00:00:00Z",
    updatedAt: "2026-07-16T00:00:00Z",
    archivedAt: null,
  };

  it("does not let replayed older events overwrite newer live state", () => {
    const live = workspaceReducer(initialWorkspaceState, {
      type: "event",
      event: event(20, item("completed", "final")),
    });
    const replayed = workspaceReducer(live, {
      type: "event",
      event: event(10, item("in_progress", "partial")),
    });

    expect(replayed.items[0].status).toBe("completed");
    expect(replayed.items[0].payload.text).toBe("final");
    expect(replayed.lastSequence).toBe(20);
  });

  it("projects Goal outcome from the same versioned event", () => {
    const goal: Goal = {
      id: "goal-1",
      threadId: thread.id,
      objective: "Ship",
      status: "complete",
      tokenBudget: null,
      tokensUsed: 12,
      timeUsedSeconds: 4,
      skillIds: [],
      createdAt: "2026-07-16T00:00:00Z",
      updatedAt: "2026-07-16T00:00:04Z",
    };
    const outcome: GoalOutcome = {
      status: "complete",
      reason: "Focused tests passed.",
      source: "agent",
      decidedByTurnId: turn.id,
      decidedAt: "2026-07-16T00:00:03Z",
      evidenceRefs: [],
    };

    const projected = workspaceReducer(initialWorkspaceState, {
      type: "event",
      event: {
        eventId: "event-goal",
        sequence: 7,
        type: "goal.updated",
        threadId: thread.id,
        turnId: turn.id,
        itemId: null,
        timestamp: "2026-07-16T00:00:04Z",
        payload: {
          goal: goal as unknown as JsonValue,
          outcome: outcome as unknown as JsonValue,
        },
      },
    });

    expect(projected.goal).toEqual(goal);
    expect(projected.goalOutcome).toEqual(outcome);
  });

  it("rebuilds streamed assistant text from compact delta events", () => {
    const created = workspaceReducer(initialWorkspaceState, {
      type: "event",
      event: event(1, item("in_progress", "Hello")),
    });
    const projected = workspaceReducer(created, {
      type: "event",
      event: {
        eventId: "event-2",
        sequence: 2,
        type: "item.delta",
        threadId: turn.threadId,
        turnId: turn.id,
        itemId: "item-1",
        timestamp: "2026-07-16T00:00:01Z",
        payload: {
          delta: " world",
          summary: "Hello world",
          streaming: true,
          updatedAt: "2026-07-16T00:00:01Z",
        },
      },
    });
    const duplicate = workspaceReducer(projected, {
      type: "event",
      event: {
        eventId: "event-2",
        sequence: 2,
        type: "item.delta",
        threadId: turn.threadId,
        turnId: turn.id,
        itemId: "item-1",
        timestamp: "2026-07-16T00:00:01Z",
        payload: { delta: " world" },
      },
    });

    expect(projected.items[0].payload.text).toBe("Hello world");
    expect(projected.items[0].summary).toBe("Hello world");
    expect(duplicate.items[0].payload.text).toBe("Hello world");
  });

  it("rebuilds typed reasoning channels without mixing them into answer text", () => {
    const reasoning: Item = {
      ...item("in_progress", "Thinking"),
      id: "reasoning-1",
      kind: "reasoning_summary",
      payload: {
        schemaVersion: 1,
        summaryText: "",
        traceText: "",
        availability: "available",
        effort: "high",
        durationMs: null,
        streaming: true,
      },
    };
    const created = workspaceReducer(initialWorkspaceState, {
      type: "event",
      event: event(1, reasoning),
    });
    const summary = workspaceReducer(created, {
      type: "event",
      event: {
        eventId: "reasoning-summary",
        sequence: 2,
        type: "item.delta",
        threadId: turn.threadId,
        turnId: turn.id,
        itemId: reasoning.id,
        timestamp: "2026-07-16T00:00:01Z",
        payload: {
          delta: "Checked constraints.",
          reasoningChannel: "summary",
          streaming: true,
        },
      },
    });
    const trace = workspaceReducer(summary, {
      type: "event",
      event: {
        eventId: "reasoning-trace",
        sequence: 3,
        type: "item.delta",
        threadId: turn.threadId,
        turnId: turn.id,
        itemId: reasoning.id,
        timestamp: "2026-07-16T00:00:02Z",
        payload: {
          delta: "Provider trace.",
          reasoningChannel: "provider_trace",
          streaming: true,
        },
      },
    });

    expect(trace.items[0].payload.summaryText).toBe("Checked constraints.");
    expect(trace.items[0].payload.traceText).toBe("Provider trace.");
    expect(trace.items[0].payload.text).toBeUndefined();
  });

  it("ignores malformed untyped deltas for reasoning items", () => {
    const reasoning: Item = {
      ...item("in_progress", "Thinking"),
      id: "reasoning-invalid",
      kind: "reasoning_summary",
      payload: {
        schemaVersion: 1,
        summaryText: "Stable",
        traceText: "",
        availability: "available",
        streaming: true,
      },
    };
    const created = workspaceReducer(initialWorkspaceState, {
      type: "event",
      event: event(1, reasoning),
    });
    const projected = workspaceReducer(created, {
      type: "event",
      event: {
        eventId: "reasoning-invalid-delta",
        sequence: 2,
        type: "item.delta",
        threadId: turn.threadId,
        turnId: turn.id,
        itemId: reasoning.id,
        timestamp: "2026-07-16T00:00:01Z",
        payload: { delta: "must not leak into text" },
      },
    });

    expect(projected.items[0].payload.summaryText).toBe("Stable");
    expect(projected.items[0].payload.text).toBeUndefined();
  });

  it("does not let an older request snapshot overwrite a live update", () => {
    const live = workspaceReducer(initialWorkspaceState, {
      type: "event",
      event: event(20, item("completed", "final")),
    });
    const snapshotted = workspaceReducer(live, {
      type: "snapshot",
      snapshot: {
        turn,
        items: [item("in_progress", "partial")],
        approvals: [],
      },
    });

    expect(snapshotted.items[0].status).toBe("completed");
    expect(snapshotted.items[0].payload.text).toBe("final");
  });

  it("projects workflow state from the same durable event stream", () => {
    const workflow: WorkflowRun = {
      id: "workflow-1",
      threadId: turn.threadId,
      turnId: turn.id,
      kind: "paper2code",
      status: "running",
      input: { sourceType: "requirement", source: "Build", options: {} },
      result: {},
      attempt: 1,
      retryOf: null,
      currentStage: "testing",
      progressCurrent: 90,
      progressTotal: 100,
      checkpoint: {},
      createdAt: "2026-07-16T00:00:00Z",
      updatedAt: "2026-07-16T00:00:00Z",
      startedAt: "2026-07-16T00:00:00Z",
      completedAt: null,
      errorCode: null,
      errorMessage: null,
    };
    const projected = workspaceReducer(initialWorkspaceState, {
      type: "event",
      event: {
        eventId: "event-workflow",
        sequence: 21,
        type: "workflow.updated",
        threadId: turn.threadId,
        turnId: turn.id,
        itemId: null,
        timestamp: workflow.updatedAt,
        payload: { workflow: workflow as unknown as JsonValue },
      },
    });

    expect(projected.workflows).toEqual([workflow]);
    expect(projected.lastSequence).toBe(21);
  });

  it("replays structured Turn plans and ignores stale updates", () => {
    const current = workspaceReducer(initialWorkspaceState, {
      type: "event",
      event: {
        eventId: "event-plan-2",
        sequence: 22,
        type: "turn.plan.updated",
        threadId: turn.threadId,
        turnId: turn.id,
        itemId: null,
        timestamp: "2026-07-16T00:00:02Z",
        payload: {
          plan: {
            explanation: "Implementing",
            steps: [
              { step: "Inspect", status: "completed" },
              { step: "Implement", status: "in_progress" },
            ],
          },
        },
      },
    });
    const stale = workspaceReducer(current, {
      type: "event",
      event: {
        eventId: "event-plan-1",
        sequence: 21,
        type: "turn.plan.updated",
        threadId: turn.threadId,
        turnId: turn.id,
        itemId: null,
        timestamp: "2026-07-16T00:00:01Z",
        payload: {
          plan: {
            explanation: "Starting",
            steps: [
              { step: "Inspect", status: "in_progress" },
              { step: "Implement", status: "pending" },
            ],
          },
        },
      },
    });

    expect(stale.plansByTurnId[turn.id]).toEqual({
      turnId: turn.id,
      explanation: "Implementing",
      steps: [
        { step: "Inspect", status: "completed" },
        { step: "Implement", status: "in_progress" },
      ],
      updatedAt: "2026-07-16T00:00:02Z",
    });
    expect(stale.lastSequence).toBe(22);
  });

  it("keeps the global Session index when project context changes", () => {
    const changed = workspaceReducer(
      {
        ...initialWorkspaceState,
        threads: [thread],
        selectedProjectId: thread.projectId,
        selectedThreadId: thread.id,
      },
      { type: "select-project", projectId: "project-2" },
    );

    expect(changed.threads).toEqual([thread]);
    expect(changed.selectedProjectId).toBe("project-2");
    expect(changed.selectedThreadId).toBeNull();
  });

  it("clears only the selected Session trace when a Session is removed", () => {
    const removed = workspaceReducer(
      {
        ...initialWorkspaceState,
        threads: [thread],
        selectedThreadId: thread.id,
        turns: [turn],
        items: [item("completed", "done")],
        plansByTurnId: {
          [turn.id]: {
            turnId: turn.id,
            explanation: null,
            steps: [{ step: "Done", status: "completed" }],
            updatedAt: "2026-07-16T00:00:00Z",
          },
        },
        lastSequence: 4,
      },
      { type: "thread-remove", threadId: thread.id },
    );

    expect(removed.threads).toEqual([]);
    expect(removed.selectedThreadId).toBeNull();
    expect(removed.turns).toEqual([]);
    expect(removed.items).toEqual([]);
    expect(removed.plansByTurnId).toEqual({});
    expect(removed.lastSequence).toBe(0);
  });
});
