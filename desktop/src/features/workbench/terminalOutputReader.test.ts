import { describe, expect, it, vi } from "vitest";
import type {
  MethodParams,
  MethodResults,
  TerminalInfo,
} from "../../generated/app-server";
import type { RpcMethod, RpcTransport } from "../../rpc/contracts";
import { TerminalOutputReader } from "./terminalOutputReader";

const terminal: TerminalInfo = {
  terminalId: "term_test",
  threadId: "thread-1",
  pid: 1,
  rows: 24,
  columns: 80,
  workspacePath: "/tmp",
};
type Page = MethodResults["terminal/read"];
function page(offset: number, data: string, other: Partial<Page> = {}): Page {
  const next = offset + new TextEncoder().encode(data).length;
  return {
    threadId: terminal.threadId,
    terminalId: terminal.terminalId,
    offset,
    data,
    nextOffset: next,
    headOffset: next,
    availableFrom: 0,
    hasMore: false,
    truncated: false,
    exited: false,
    exitCode: null,
    ...other,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { resolve, promise };
}
class Runtime implements RpcTransport {
  calls: MethodParams["terminal/read"][] = [];
  constructor(
    private read: (params: MethodParams["terminal/read"]) => Promise<Page>,
  ) {}
  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    // Any create/write/close during recovery fails the test.
    expect(method).toBe("terminal/read");
    const input = params as MethodParams["terminal/read"];
    this.calls.push(input);
    return this.read(input) as Promise<MethodResults[M]>;
  }
}
function reader(
  runtime: Runtime,
  write = vi.fn(async (data: string): Promise<void> => {
    void data;
  }),
) {
  const truncated = vi.fn(),
    exit = vi.fn(),
    error = vi.fn();
  return {
    stream: new TerminalOutputReader(
      runtime,
      terminal,
      write,
      truncated,
      exit,
      error,
    ),
    write,
    truncated,
    exit,
    error,
  };
}

describe("terminal output recovery", () => {
  it("uses UTF-8 byte cursors and ignores overlapping notifications", async () => {
    const first = deferred<Page>();
    const runtime = new Runtime(async ({ offset }) =>
      offset === 0
        ? first.promise
        : page(7, "!", { exited: true, exitCode: 0 }),
    );
    const test = reader(runtime);
    const done = test.stream.recover();
    test.stream.receive(8);
    first.resolve(page(0, "汉🙂"));
    await done;
    test.stream.receive(7);
    test.stream.receive(8);
    expect(test.write.mock.calls.map(([data]) => data).join("")).toBe("汉🙂!");
    expect(runtime.calls.map((call) => call.offset)).toEqual([0, 7]);
    expect(test.exit).toHaveBeenCalledExactlyOnceWith(0);
  });

  it("finishes captured windows before catching up, then reports exit after the final byte", async () => {
    const runtime = new Runtime(async ({ offset }) =>
      offset === 0
        ? page(0, "a", { headOffset: 4, hasMore: true })
        : offset === 1
          ? page(1, "bcd", { headOffset: 8, exited: true, exitCode: 0 })
          : page(4, "efgh", { exited: true, exitCode: 0 }),
    );
    const test = reader(runtime);
    await test.stream.recover();
    expect(runtime.calls.map((call) => call.through)).toEqual([
      undefined,
      4,
      undefined,
    ]);
    expect(test.write.mock.calls.map(([data]) => data).join("")).toBe(
      "abcdefgh",
    );
    expect(test.exit).toHaveBeenCalledOnce();
    expect(test.exit.mock.invocationCallOrder[0]).toBeGreaterThan(
      test.write.mock.invocationCallOrder.at(-1)!,
    );
  });

  it("marks an evicted window once and resumes from the retained byte boundary", async () => {
    const runtime = new Runtime(async ({ offset }) =>
      offset === 0
        ? page(100, "汉", {
            truncated: true,
            availableFrom: 100,
            exited: true,
            exitCode: 0,
          })
        : page(103, "", { availableFrom: 100, exited: true, exitCode: 0 }),
    );
    const test = reader(runtime);
    await test.stream.recover();
    expect(test.truncated).toHaveBeenCalledOnce();
    expect(test.write).toHaveBeenCalledExactlyOnceWith("汉");
    expect(test.exit).toHaveBeenCalledOnce();
    expect(runtime.calls.map((call) => call.offset)).toEqual([0, 103]);
  });

  it("coalesces warnings and checks for a lost exit notification", async () => {
    const pending = deferred<Page>();
    const runtime = new Runtime(async ({ offset }) =>
      offset === 0
        ? pending.promise
        : page(1, "", { exited: true, exitCode: 0 }),
    );
    const test = reader(runtime);
    const done = test.stream.recover();
    for (let index = 0; index < 50; index++)
      expect(test.stream.recover()).toBe(done);
    expect(runtime.calls).toHaveLength(1);
    pending.resolve(page(0, "x"));
    await done;
    expect(runtime.calls).toHaveLength(2);
    expect(test.exit).toHaveBeenCalledOnce();
  });

  it("waits for the renderer and cancels a blocked write on detach", async () => {
    const render = deferred<void>();
    const runtime = new Runtime(async () =>
      page(0, "a", { hasMore: true, headOffset: 2 }),
    );
    const write = vi.fn(async (text: string) => {
      void text;
      return render.promise;
    });
    const test = reader(runtime, write);
    const done = test.stream.recover();
    await vi.waitFor(() => expect(write).toHaveBeenCalledOnce());
    expect(runtime.calls).toHaveLength(1);
    test.stream.stop();
    await done;
    expect(runtime.calls).toHaveLength(1);
    render.resolve();
  });

  it("retries only the unread output after a read failure", async () => {
    let calls = 0;
    const runtime = new Runtime(async () => {
      calls++;
      if (calls === 1) return page(0, "a", { headOffset: 2, hasMore: true });
      if (calls === 2) throw new Error("disconnected");
      return page(1, "b");
    });
    const test = reader(runtime);
    await expect(test.stream.recover()).rejects.toThrow("disconnected");
    await test.stream.recover();
    expect(test.write.mock.calls.map(([data]) => data)).toEqual(["a", "b"]);
    expect(runtime.calls.map((call) => call.offset)).toEqual([0, 1, 1]);
  });

  it("reduces pages to fit a small transport frame", async () => {
    const runtime = new Runtime(async ({ limit = 0 }) => {
      if (limit > 128) throw { code: "RESPONSE_TOO_LARGE" };
      return page(0, "🙂");
    });
    const test = reader(runtime);
    await test.stream.recover();
    expect(test.write).toHaveBeenCalledExactlyOnceWith("🙂");
    expect(runtime.calls.at(-1)?.limit).toBe(128);
  });

  it.each([
    page(0, "", { headOffset: 1 }),
    page(0, "a", { nextOffset: 3, headOffset: 3 }),
    page(0, "", { hasMore: true }),
  ])("rejects broken cursors instead of spinning", async (broken) => {
    const runtime = new Runtime(async () => broken);
    await expect(reader(runtime).stream.recover()).rejects.toThrow(
      "Terminal output",
    );
    expect(runtime.calls).toHaveLength(1);
  });
});
