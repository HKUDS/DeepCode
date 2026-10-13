import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listen: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@tauri-apps/plugin-dialog", () => ({
  confirm: vi.fn(),
  open: vi.fn(),
}));

import type { AnyRpcNotification } from "./contracts";
import {
  DesktopRuntimeError,
  normalizeBridgeError,
  normalizeUpdaterError,
  tauriRuntime,
} from "./tauriRuntime";

const note = {
  jsonrpc: "2.0",
  method: "skills.changed",
  params: { projectId: "project-1" },
} as unknown as AnyRpcNotification;

function listening(unlisten: () => Promise<void>) {
  let deliver!: (event: { payload: unknown }) => void;
  mocks.listen.mockImplementation(
    async (_event: string, handler: typeof deliver) => {
      deliver = handler;
      return unlisten;
    },
  );
  return (payload: unknown) => deliver({ payload });
}

describe("Tauri runtime errors", () => {
  it("preserves stable bridge metadata while remaining a real Error", () => {
    const error = normalizeBridgeError({
      code: "NOT_FOUND",
      message: "Thread not found",
      retryable: false,
      data: { threadId: "missing" },
    });

    expect(error).toBeInstanceOf(Error);
    expect(error).toBeInstanceOf(DesktopRuntimeError);
    expect(error.code).toBe("NOT_FOUND");
    expect(error.message).toBe("Thread not found");
    expect(error.retryable).toBe(false);
    expect(error.data).toEqual({ threadId: "missing" });
  });

  it("reports an intentionally unconfigured update channel clearly", () => {
    const error = normalizeUpdaterError(
      "Updater does not have any endpoints set.",
    );

    expect(error).toBeInstanceOf(Error);
    expect(error.code).toBe("UPDATER_NOT_CONFIGURED");
    expect(error.message).toBe(
      "This build does not configure a signed update channel.",
    );
    expect(error.retryable).toBe(false);
  });
});

describe("Tauri runtime listeners", () => {
  afterEach(() => mocks.listen.mockReset());

  it("stops delivering at once and unlistens only once", async () => {
    const unlisten = vi.fn(async () => undefined);
    const deliver = listening(unlisten);
    const receive = vi.fn();
    const stop = await tauriRuntime.onNotification(receive);

    deliver(note);
    stop();
    stop();
    deliver(note);

    expect(receive).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(unlisten).toHaveBeenCalled());
    expect(unlisten).toHaveBeenCalledTimes(1);
  });

  it("retries an unlisten that ran before Tauri registered the listener", async () => {
    // Tauri adds the listener in the webview after listen() resolves; an
    // unlisten right away rejects with "listeners[eventId] is undefined".
    const unlisten = vi
      .fn<() => Promise<void>>()
      .mockRejectedValueOnce(new TypeError("listeners[eventId] is undefined"))
      .mockResolvedValue(undefined);
    listening(unlisten);
    const stop = await tauriRuntime.onStatus(vi.fn());

    stop();

    await vi.waitFor(() => expect(unlisten).toHaveBeenCalledTimes(2));
  });
});
