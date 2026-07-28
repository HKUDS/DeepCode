import { describe, expect, it } from "vitest";

import type {
  MethodParams,
  MethodResults,
  Turn,
} from "../generated/app-server";
import type {
  BridgeError,
  DesktopRuntime,
  RpcMethod,
} from "../rpc/contracts";
import {
  latestExecutingTurn,
  sendInteractiveTurn,
} from "./interactiveTurnRouter";

interface RecordedCall {
  method: RpcMethod;
  params: unknown;
}

type Step =
  | { method: RpcMethod; result: unknown }
  | { method: RpcMethod; error: BridgeError };

function scriptedRuntime(steps: Step[], calls: RecordedCall[]): DesktopRuntime {
  const transport: Pick<DesktopRuntime, "request"> = {
    async request<M extends RpcMethod>(
      method: M,
      params: MethodParams[M],
    ): Promise<MethodResults[M]> {
      calls.push({ method, params });
      const step = steps.shift();
      expect(step?.method).toBe(method);
      if (!step) throw new Error(`Unexpected request: ${method}`);
      if ("error" in step) throw step.error;
      return step.result as MethodResults[M];
    },
  };
  return transport as DesktopRuntime;
}

function turn(id: string, status: Turn["status"], ordinal = 1): Turn {
  return {
    id,
    threadId: "thread-1",
    ordinal,
    prompt: "work",
    status,
    stopReason: null,
    errorCode: null,
    errorMessage: null,
    startedAt: status === "queued" ? null : "2026-07-27T00:00:00Z",
    completedAt: null,
  };
}

function steerResult(active: Turn) {
  return {
    messageId: "message-1",
    delivery: "current_turn" as const,
    duplicate: false,
    turn: active,
  };
}

function startResult(started: Turn) {
  return { turn: started, items: [], approvals: [] };
}

function bridgeError(
  code: string,
  actualTurnId: string | null,
): BridgeError {
  return {
    code,
    message: code,
    retryable: true,
    data: {
      code,
      retryable: true,
      correlationId: "test",
      details: { actualTurnId },
    },
  };
}

describe("sendInteractiveTurn", () => {
  it("steers the cached active Turn", async () => {
    const calls: RecordedCall[] = [];
    const active = turn("turn-a", "running");
    const runtime = scriptedRuntime(
      [{ method: "turn/steer", result: steerResult(active) }],
      calls,
    );

    const result = await sendInteractiveTurn(runtime, {
      threadId: "thread-1",
      prompt: "correct the approach",
      cachedActiveTurnId: active.id,
      messageId: "message-1",
    });

    expect(result.delivery).toBe("steered");
    expect(calls).toEqual([
      {
        method: "turn/steer",
        params: {
          threadId: "thread-1",
          expectedTurnId: "turn-a",
          prompt: "correct the approach",
          messageId: "message-1",
        },
      },
    ]);
  });

  it("starts once with the same message when the cached Turn has ended", async () => {
    const calls: RecordedCall[] = [];
    const started = turn("turn-b", "queued", 2);
    const runtime = scriptedRuntime(
      [
        {
          method: "turn/steer",
          error: bridgeError("NO_ACTIVE_TURN", null),
        },
        { method: "turn/start", result: startResult(started) },
      ],
      calls,
    );

    const result = await sendInteractiveTurn(runtime, {
      threadId: "thread-1",
      prompt: "continue from here",
      cachedActiveTurnId: "turn-a",
      skillIds: ["skill-1"],
      messageId: "message-1",
    });

    expect(result.delivery).toBe("started");
    expect(calls.map((call) => call.method)).toEqual([
      "turn/steer",
      "turn/start",
    ]);
    expect(calls[1]?.params).toMatchObject({
      messageId: "message-1",
      skills: ["skill-1"],
    });
  });

  it("steers a continuation that wins the no-active fallback race", async () => {
    const calls: RecordedCall[] = [];
    const actual = turn("turn-continuation", "running", 2);
    const runtime = scriptedRuntime(
      [
        {
          method: "turn/steer",
          error: bridgeError("NO_ACTIVE_TURN", null),
        },
        {
          method: "turn/start",
          error: bridgeError("TURN_ALREADY_ACTIVE", actual.id),
        },
        { method: "turn/steer", result: steerResult(actual) },
      ],
      calls,
    );

    const result = await sendInteractiveTurn(runtime, {
      threadId: "thread-1",
      prompt: "apply this to the continuation",
      cachedActiveTurnId: "turn-old",
      messageId: "message-1",
    });

    expect(result.delivery).toBe("steered");
    expect(calls.map((call) => call.method)).toEqual([
      "turn/steer",
      "turn/start",
      "turn/steer",
    ]);
    expect(calls.map((call) => call.params)).toEqual([
      expect.objectContaining({ messageId: "message-1" }),
      expect.objectContaining({ messageId: "message-1" }),
      expect.objectContaining({
        expectedTurnId: "turn-continuation",
        messageId: "message-1",
      }),
    ]);
  });

  it("resynchronizes an expected-Turn mismatch exactly once", async () => {
    const calls: RecordedCall[] = [];
    const actual = turn("turn-b", "running", 2);
    const runtime = scriptedRuntime(
      [
        {
          method: "turn/steer",
          error: bridgeError("EXPECTED_TURN_MISMATCH", actual.id),
        },
        { method: "turn/steer", result: steerResult(actual) },
      ],
      calls,
    );

    const result = await sendInteractiveTurn(runtime, {
      threadId: "thread-1",
      prompt: "use the current Turn",
      cachedActiveTurnId: "turn-a",
      messageId: "message-1",
    });

    expect(result.turn.id).toBe("turn-b");
    expect(calls.map((call) => call.params)).toEqual([
      expect.objectContaining({
        expectedTurnId: "turn-a",
        messageId: "message-1",
      }),
      expect.objectContaining({
        expectedTurnId: "turn-b",
        messageId: "message-1",
      }),
    ]);
  });

  it("steers a Turn that appears during an idle start race", async () => {
    const calls: RecordedCall[] = [];
    const actual = turn("turn-b", "running", 2);
    const runtime = scriptedRuntime(
      [
        {
          method: "turn/start",
          error: bridgeError("TURN_ALREADY_ACTIVE", actual.id),
        },
        { method: "turn/steer", result: steerResult(actual) },
      ],
      calls,
    );

    const result = await sendInteractiveTurn(runtime, {
      threadId: "thread-1",
      prompt: "new input",
      cachedActiveTurnId: null,
      messageId: "message-1",
    });

    expect(result.delivery).toBe("steered");
    expect(calls.map((call) => call.method)).toEqual([
      "turn/start",
      "turn/steer",
    ]);
  });

  it("does not loop after the one corrective attempt", async () => {
    const calls: RecordedCall[] = [];
    const runtime = scriptedRuntime(
      [
        {
          method: "turn/steer",
          error: bridgeError("EXPECTED_TURN_MISMATCH", "turn-b"),
        },
        {
          method: "turn/steer",
          error: bridgeError("EXPECTED_TURN_MISMATCH", "turn-c"),
        },
      ],
      calls,
    );

    await expect(
      sendInteractiveTurn(runtime, {
        threadId: "thread-1",
        prompt: "one input",
        cachedActiveTurnId: "turn-a",
        messageId: "message-1",
      }),
    ).rejects.toMatchObject({ code: "EXPECTED_TURN_MISMATCH" });
    expect(calls).toHaveLength(2);
  });
});

describe("latestExecutingTurn", () => {
  it("ignores queued and other-thread Turns and chooses the latest execution", () => {
    const other = { ...turn("other", "running", 9), threadId: "thread-2" };
    expect(
      latestExecutingTurn(
        [
          turn("queued", "queued", 10),
          turn("older", "running", 1),
          turn("latest", "waiting_approval", 3),
          other,
        ],
        "thread-1",
      )?.id,
    ).toBe("latest");
  });
});
