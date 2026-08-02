import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  isTauri: vi.fn(),
  nativeConfirm: vi.fn(),
}));

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: mocks.isTauri,
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({
  confirm: mocks.nativeConfirm,
}));

import { confirmAction } from "./confirmAction";

describe("confirmAction", () => {
  beforeEach(() => {
    mocks.isTauri.mockReturnValue(false);
    mocks.nativeConfirm.mockReset();
  });

  afterEach(() => vi.restoreAllMocks());

  it("uses the synchronous browser dialog outside Tauri", async () => {
    const browserConfirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    await expect(confirmAction("Continue?")).resolves.toBe(true);

    expect(browserConfirm).toHaveBeenCalledWith("Continue?");
    expect(mocks.nativeConfirm).not.toHaveBeenCalled();
  });

  it("awaits the native Tauri dialog with explicit destructive labels", async () => {
    mocks.isTauri.mockReturnValue(true);
    mocks.nativeConfirm.mockResolvedValue(false);
    const browserConfirm = vi.spyOn(window, "confirm");

    await expect(
      confirmAction("Remove it?", {
        confirmLabel: "Remove",
        cancelLabel: "Keep",
      }),
    ).resolves.toBe(false);

    expect(browserConfirm).not.toHaveBeenCalled();
    expect(mocks.nativeConfirm).toHaveBeenCalledWith("Remove it?", {
      title: "DeepCode",
      kind: "warning",
      okLabel: "Remove",
      cancelLabel: "Keep",
    });
  });
});
