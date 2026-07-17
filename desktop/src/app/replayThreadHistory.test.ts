import { describe, expect, it } from "vitest";

import type {
  Event,
  MethodParams,
  MethodResults,
} from "../generated/app-server";
import type { RpcMethod, RpcTransport } from "../rpc/contracts";
import { replayThreadHistory } from "./replayThreadHistory";

function event(sequence: number): Event {
  return {
    eventId: `event-${sequence}`,
    sequence,
    type: "turn.updated",
    threadId: "thread-1",
    turnId: null,
    itemId: null,
    timestamp: "2026-07-17T00:00:00Z",
    payload: {},
  };
}

class ReplayRuntime implements RpcTransport {
  readonly limits: number[] = [];
  readonly afters: number[] = [];

  constructor(
    private readonly replay: (
      params: MethodParams["event/replay"],
    ) => Promise<MethodResults["event/replay"]>,
  ) {}

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    if (method !== "event/replay") {
      throw new Error(`Unexpected method: ${method}`);
    }
    const replayParams = params as MethodParams["event/replay"];
    this.limits.push(replayParams.limit ?? 500);
    this.afters.push(replayParams.after ?? 0);
    return this.replay(replayParams) as Promise<MethodResults[M]>;
  }
}

describe("replayThreadHistory", () => {
  it("continues from the server cursor even when a byte-bounded page is short", async () => {
    const runtime = new ReplayRuntime(async ({ after }) =>
      after === 0
        ? { events: [event(1), event(2)], nextAfter: 2, hasMore: true }
        : { events: [event(3)], nextAfter: null, hasMore: false },
    );
    const received: number[] = [];

    await replayThreadHistory(runtime, "thread-1", (value) => {
      received.push(value.sequence);
    });

    expect(received).toEqual([1, 2, 3]);
    expect(runtime.afters).toEqual([0, 2]);
  });

  it("shrinks the requested page for an older server that rejects large responses", async () => {
    const runtime = new ReplayRuntime(async ({ limit = 500 }) => {
      if (limit > 125) {
        throw {
          code: "RESPONSE_TOO_LARGE",
          message: "response exceeds the configured message limit",
        };
      }
      return {
        events: [event(1)],
      } as MethodResults["event/replay"];
    });
    const received: number[] = [];

    await replayThreadHistory(runtime, "thread-1", (value) => {
      received.push(value.sequence);
    });

    expect(received).toEqual([1]);
    expect(runtime.limits).toEqual([1000, 500, 250, 125]);
  });

  it("fails instead of looping when a server cursor does not advance", async () => {
    const runtime = new ReplayRuntime(async () => ({
      events: [],
      nextAfter: 0,
      hasMore: true,
    }));

    await expect(
      replayThreadHistory(runtime, "thread-1", () => undefined),
    ).rejects.toThrow("event/replay did not advance its cursor");
  });
});
