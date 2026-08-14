import { describe, expect, it } from "vitest";

import {
  DesktopRuntimeError,
  normalizeBridgeError,
  normalizeUpdaterError,
} from "./tauriRuntime";

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
