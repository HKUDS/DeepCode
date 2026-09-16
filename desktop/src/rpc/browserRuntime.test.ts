import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserRuntime } from "./browserRuntime";

const handshake = {
  protocolVersion: "1.0",
  serverInfo: { name: "deepcode", version: "test" },
  clientInfo: { name: "web", version: "1" },
  serviceInfo: { instanceId: "instance-1", frontendBuildId: "build-1" },
  capabilities: {
    maxMessageBytes: 1024 * 1024,
    methods: ["project/list"],
    requestRetry: {
      default: "never",
      readMethods: ["project/list"],
      keyedMethods: { "turn/start": "messageId", "turn/steer": "messageId" },
    },
  },
};
type Frame = { id: number; method: string; params: Record<string, unknown> };
let handle: (socket: Socket, frame: Frame) => void;
let requests: Frame[];
class Socket {
  static OPEN = 1;
  readyState = 0;
  onopen?: () => void;
  onclose?: () => void;
  onmessage?: (event: { data: string }) => void;
  onerror?: () => void;
  constructor() {
    queueMicrotask(() => {
      if (this.readyState === 0) {
        this.readyState = 1;
        this.onopen?.();
      }
    });
  }
  send(raw: string) {
    const frame = JSON.parse(raw) as Frame;
    requests.push(frame);
    handle(this, frame);
  }
  result(frame: Frame, result: unknown) {
    queueMicrotask(() =>
      this.onmessage?.({ data: JSON.stringify({ id: frame.id, result }) }),
    );
  }
  close() {
    this.readyState = 3;
    queueMicrotask(() => this.onclose?.());
  }
}
let runtime: BrowserRuntime;
beforeEach(() => {
  vi.useFakeTimers();
  requests = [];
  history.replaceState(null, "", "/");
  vi.stubGlobal("WebSocket", Socket);
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ authenticated: true }), { status: 200 }),
    ),
  );
  handle = (socket, frame) =>
    socket.result(
      frame,
      frame.method === "initialize" ? handshake : { projects: [] },
    );
  runtime = new BrowserRuntime({
    chooseDirectory: async () => null,
    buildId: "build-1",
    requestTimeout: 1000,
    reconnectDelays: [10, 20],
  });
});
afterEach(async () => {
  runtime.dispose();
  await vi.advanceTimersByTimeAsync(0);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("BrowserRuntime connection and retry contract", () => {
  it("exchanges a fragment ticket once and removes it before fetching", async () => {
    runtime.dispose();
    history.replaceState(null, "", "/#ticket=one-time");
    runtime = new BrowserRuntime({ chooseDirectory: async () => null });
    expect(location.hash).toBe("");
    expect((await runtime.status()).phase).toBe("ready");
    expect(fetch).toHaveBeenCalledWith(
      "/auth/exchange",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ ticket: "one-time" }),
      }),
    );
    await runtime.restart();
    expect(
      vi.mocked(fetch).mock.calls.filter(([path]) => path === "/auth/exchange"),
    ).toHaveLength(1);
  });

  it("reconnects and retries a lost read with bounded attempts", async () => {
    await runtime.status();
    let calls = 0;
    handle = (socket, frame) => {
      if (frame.method === "initialize") socket.result(frame, handshake);
      else if (++calls === 1) socket.close();
      else socket.result(frame, { projects: [] });
    };
    const result = runtime.request("project/list", {});
    await vi.advanceTimersByTimeAsync(1000);
    await expect(result).resolves.toEqual({ projects: [] });
    expect(calls).toBe(2);
  });

  it("retries keyed submissions with the original frozen intent", async () => {
    await runtime.status();
    let calls = 0;
    handle = (socket, frame) => {
      if (frame.method === "initialize") socket.result(frame, handshake);
      else if (++calls === 1) socket.close();
      else socket.result(frame, { turn: { id: "original" } });
    };
    const params = {
      threadId: "thread",
      messageId: "same",
      prompt: "task",
      model: "original-model",
    };
    const result = runtime.request("turn/start", params);
    params.model = "changed-after-send";
    await vi.advanceTimersByTimeAsync(1000);
    await result;
    const submissions = requests.filter(
      (frame) => frame.method === "turn/start",
    );
    expect(submissions).toHaveLength(2);
    expect(submissions[0].params).toEqual(submissions[1].params);
    expect(submissions[1].params.model).toBe("original-model");
  });

  it.each([
    "settings/update",
    "terminal/write",
    "thread/goal/set",
    "approval/respond",
  ] as const)("does not resend an uncertain %s", async (method) => {
    await runtime.status();
    handle = (socket, frame) =>
      frame.method === "initialize"
        ? socket.result(frame, handshake)
        : socket.close();
    // The transport is intentionally tested independently of each method's schema.
    const request = runtime.request(method, {} as never);
    const rejected = expect(request).rejects.toMatchObject({
      code: "RESULT_UNKNOWN",
      retryable: false,
    });
    await vi.advanceTimersByTimeAsync(1000);
    await rejected;
    expect(requests.filter((frame) => frame.method === method)).toHaveLength(1);
  });

  it("does not queue terminal input while offline", async () => {
    await expect(
      runtime.request("terminal/write", {
        threadId: "thread",
        terminalId: "term_test",
        data: "echo do-not-send\r",
      }),
    ).rejects.toMatchObject({ code: "NOT_CONNECTED" });
    await runtime.status();
    expect(requests.some((frame) => frame.method === "terminal/write")).toBe(
      false,
    );
  });

  it("does not reuse an old retry promise after capability negotiation changes", async () => {
    await runtime.status();
    handle = (socket, frame) =>
      frame.method === "initialize"
        ? socket.result(frame, {
            ...handshake,
            capabilities: {
              ...handshake.capabilities,
              requestRetry: {
                ...handshake.capabilities.requestRetry,
                keyedMethods: {},
              },
            },
          })
        : socket.close();
    const result = runtime.request("turn/start", {
      threadId: "thread",
      messageId: "same",
      prompt: "task",
    });
    const rejected = expect(result).rejects.toMatchObject({
      code: "RESULT_UNKNOWN",
    });
    await vi.advanceTimersByTimeAsync(1000);
    await rejected;
    expect(
      requests.filter((frame) => frame.method === "turn/start"),
    ).toHaveLength(1);
  });

  it("stops on an expired session instead of requesting a new management credential", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("expired", { status: 401 }),
    );
    expect(await runtime.status()).toMatchObject({
      phase: "stopped",
      errorCode: "AUTH_REQUIRED",
      message: expect.stringContaining("deepcode web"),
    });
    await vi.advanceTimersByTimeAsync(30000);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(requests).toEqual([]);
  });

  it("clears the authorization error when a valid browser session is available", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("expired", { status: 401 }));
    expect((await runtime.status()).errorCode).toBe("AUTH_REQUIRED");
    expect(await runtime.restart()).toMatchObject({ phase: "ready", errorCode: null, message: null });
  });

  it("reports network failures separately and recovers with bounded retries", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    expect(await runtime.status()).toMatchObject({ phase: "stopped", errorCode: "CONNECTION_LOST" });
    await vi.advanceTimersByTimeAsync(10);
    expect(await runtime.status()).toMatchObject({ phase: "ready", errorCode: null });
  });

  it("reports sign-out as browser authorization rather than a stopped service", async () => {
    await runtime.status();
    await runtime.logout();
    expect(await runtime.status()).toMatchObject({ phase: "stopped", errorCode: "AUTH_REQUIRED" });
  });

  it("rejects an incompatible frontend build", async () => {
    handle = (socket, frame) =>
      socket.result(frame, {
        ...handshake,
        serviceInfo: {
          ...handshake.serviceInfo,
          frontendBuildId: "other-build",
        },
      });
    expect((await runtime.status()).message).toContain("different builds");
    expect(requests.filter((frame) => frame.method !== "initialize")).toEqual(
      [],
    );
  });

  it("settles an unknown write timeout without retrying it", async () => {
    await runtime.status();
    handle = () => {};
    const result = runtime.request("terminal/create", { threadId: "thread" });
    const rejected = expect(result).rejects.toMatchObject({
      code: "RESULT_UNKNOWN",
    });
    await vi.advanceTimersByTimeAsync(1001);
    await rejected;
    expect(
      requests.filter((frame) => frame.method === "terminal/create"),
    ).toHaveLength(1);
  });
});
