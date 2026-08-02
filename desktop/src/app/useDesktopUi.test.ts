import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useDesktopUi } from "./useDesktopUi";

describe("Desktop UI review guard", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens a concrete Inspector tab", () => {
    const { result } = renderHook(() => useDesktopUi());

    act(() => result.current.openInspector("details"));

    expect(result.current.inspectorOpen).toBe(true);
    expect(result.current.inspectorTab).toBe("details");
  });

  it("closes Review when navigating to a management destination", async () => {
    const { result } = renderHook(() => useDesktopUi());
    act(() => result.current.openInspector("files"));
    await act(async () => {
      expect(await result.current.navigateTo("skills")).toBe(true);
    });
    expect(result.current.destination).toBe("skills");
    expect(result.current.inspectorOpen).toBe(false);
  });

  it("does not close Review when the user keeps an unsaved editor draft", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { result } = renderHook(() => useDesktopUi());
    act(() => {
      result.current.openInspector("files");
      result.current.setInspectorDirty(true);
    });

    await act(async () => result.current.closeInspector());

    expect(result.current.inspectorOpen).toBe(true);
    expect(result.current.inspectorDirty).toBe(true);
  });

  it("clears the guard after the user discards the draft", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { result } = renderHook(() => useDesktopUi());
    act(() => {
      result.current.openInspector("files");
      result.current.setInspectorDirty(true);
    });

    await act(async () => result.current.closeInspector());

    expect(result.current.inspectorOpen).toBe(false);
    expect(result.current.inspectorDirty).toBe(false);
  });
});
