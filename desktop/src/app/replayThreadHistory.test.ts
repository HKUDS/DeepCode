import { describe, expect, it, vi } from "vitest";

import type {
  Event,
  MethodParams,
  MethodResults,
} from "../generated/app-server";
import type { RpcMethod, RpcTransport } from "../rpc/contracts";
import { replayThreadHistory } from "./replayThreadHistory";
import { ThreadEventStream } from "./threadEventStream";

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
  it("pins the first page's head while later events continue to arrive", async () => {
    const requests: MethodParams["event/replay"][] = [];
    const runtime = new ReplayRuntime(async (params) => {
      requests.push(params);
      return params.after === 0
        ? { events: [event(1)], nextAfter: 1, hasMore: true, headSequence: 2 }
        : {
            events: [event(2)],
            nextAfter: null,
            hasMore: false,
            headSequence: 2,
          };
    });
    const received: number[] = [];
    expect(
      await replayThreadHistory(runtime, "thread-1", (value) =>
        received.push(value.sequence),
      ),
    ).toBe(2);
    expect(requests[0].through).toBeUndefined();
    expect(requests[1].through).toBe(2);
    expect(received).toEqual([1, 2]);
  });

  it.each([
    { events: [event(1), event(3)], headSequence: 3 },
    { events: [{ ...event(1), threadId: "another" }], headSequence: 1 },
    { events: [event(1)], headSequence: 2 },
    { events: [event(1)], headSequence: 0 },
  ])("rejects missing or mismatched replay evidence", async (page) => {
    const runtime = new ReplayRuntime(async () => ({
      ...page,
      hasMore: false,
      nextAfter: null,
    }));
    await expect(
      replayThreadHistory(runtime, "thread-1", () => undefined),
    ).rejects.toThrow("event/replay");
  });

  it("does not silently accept a cursor ahead of replaced history", async () => {
    const runtime = new ReplayRuntime(async () => ({
      events: [],
      hasMore: false,
      nextAfter: null,
      headSequence: 1,
    }));
    await expect(
      replayThreadHistory(runtime, "thread-1", () => undefined, { after: 5 }),
    ).rejects.toThrow("history changed");
  });
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function page(...sequences: number[]): MethodResults["event/replay"] {
  return { events: sequences.map(event), nextAfter: null, hasMore: false };
}

describe("ThreadEventStream", () => {
  it("orders replay before overlapping live deltas and drops duplicates", async () => {
    const first = deferred<MethodResults["event/replay"]>();
    const runtime = new ReplayRuntime(async ({ after }) =>
      after === 0 ? first.promise : page(3, 4),
    );
    const accepted: number[] = [];
    const stream = new ThreadEventStream(
      runtime,
      "thread-1",
      (value) => accepted.push(value.sequence),
      vi.fn(),
    );
    const recovering = stream.recover();
    stream.receive(event(4));
    stream.receive(event(3));
    stream.receive(event(2));
    expect(accepted).toEqual([]);
    first.resolve(page(1, 2));
    await recovering;
    stream.receive(event(3));
    stream.receive(event(5));
    expect(accepted).toEqual([1, 2, 3, 4, 5]);
    expect(runtime.afters).toEqual([0, 2]);
  });

  it("repairs an out-of-order live gap from its last contiguous cursor", async () => {
    const runtime = new ReplayRuntime(async () => page(2, 3));
    const accepted: number[] = [];
    const stream = new ThreadEventStream(
      runtime,
      "thread-1",
      (value) => accepted.push(value.sequence),
      vi.fn(),
    );
    stream.receive(event(1));
    stream.receive(event(3));
    await vi.waitFor(() => expect(accepted).toEqual([1, 2, 3]));
    stream.receive(event(2));
    expect(accepted).toEqual([1, 2, 3]);
    expect(runtime.afters).toEqual([1]);
  });

  it("coalesces overflow warnings and recovers a missing tail during replay", async () => {
    const first = deferred<MethodResults["event/replay"]>();
    const runtime = new ReplayRuntime(async ({ after }) =>
      after === 0 ? first.promise : page(2),
    );
    const accepted: number[] = [];
    const stream = new ThreadEventStream(
      runtime,
      "thread-1",
      (value) => accepted.push(value.sequence),
      vi.fn(),
    );
    const initial = stream.recover();
    for (let index = 0; index < 100; index++)
      expect(stream.recover()).toBe(initial);
    expect(runtime.afters).toEqual([0]);
    first.resolve(page(1));
    await initial;
    expect(accepted).toEqual([1, 2]);
    expect(runtime.afters).toEqual([0, 1]);
  });

  it("recovers a large live burst with one in-flight replay instead of retaining live payloads", async () => {
    const first = deferred<MethodResults["event/replay"]>();
    const runtime = new ReplayRuntime(async ({ after = 0, limit = 1000 }) => {
      if (!after) return first.promise;
      const last = Math.min(after + limit, 3000);
      return {
        events: Array.from({ length: last - after }, (_, index) =>
          event(after + index + 1),
        ),
        headSequence: 3000,
        nextAfter: last < 3000 ? last : null,
        hasMore: last < 3000,
      };
    });
    const accepted: number[] = [];
    const stream = new ThreadEventStream(
      runtime,
      "thread-1",
      (value) => accepted.push(value.sequence),
      vi.fn(),
    );
    const done = stream.recover();
    for (let sequence = 3000; sequence > 0; sequence--)
      stream.receive(event(sequence));
    expect(runtime.afters).toEqual([0]);
    first.resolve(page(1));
    await done;
    expect(accepted).toEqual(
      Array.from({ length: 3000 }, (_, index) => index + 1),
    );
    expect(runtime.afters).toEqual([0, 1, 1001, 2001]);
  });

  it.each([false, true])(
    "ignores late results or errors after selection changes (%s)",
    async (fails) => {
      const pending = deferred<MethodResults["event/replay"]>();
      const runtime = new ReplayRuntime(async () => pending.promise);
      const accept = vi.fn();
      const onError = vi.fn();
      const stream = new ThreadEventStream(
        runtime,
        "thread-1",
        accept,
        onError,
      );
      const done = stream.recover();
      stream.stop();
      stream.receive(event(1));
      if (fails) pending.reject(new Error("connection closed"));
      else pending.resolve(page(1));
      await done;
      expect(accept).not.toHaveBeenCalled();
      expect(onError).not.toHaveBeenCalled();
    },
  );

  it("retains only applied progress after failure and can repair on a later warning", async () => {
    let calls = 0;
    const runtime = new ReplayRuntime(async () => {
      calls++;
      if (calls === 1) return { ...page(1), nextAfter: 1, hasMore: true };
      if (calls === 2) throw new Error("connection closed");
      return page(2, 3);
    });
    const accepted: number[] = [];
    const stream = new ThreadEventStream(
      runtime,
      "thread-1",
      (value) => accepted.push(value.sequence),
      vi.fn(),
    );
    await expect(stream.recover()).rejects.toThrow("connection closed");
    await stream.recover();
    expect(accepted).toEqual([1, 2, 3]);
    expect(runtime.afters).toEqual([0, 1, 1]);
  });

  it("surfaces irrecoverable history instead of skipping a gap or spinning", async () => {
    const runtime = new ReplayRuntime(async () => page());
    const accept = vi.fn();
    const onError = vi.fn();
    const stream = new ThreadEventStream(runtime, "thread-1", accept, onError);
    stream.receive(event(3));
    await vi.waitFor(() => expect(onError).toHaveBeenCalledOnce());
    expect(runtime.afters).toEqual([0]);
    expect(accept).not.toHaveBeenCalled();
  });
});
