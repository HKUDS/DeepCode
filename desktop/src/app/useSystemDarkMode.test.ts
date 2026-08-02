import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSystemDarkMode } from "./useSystemDarkMode";

function installMatchMedia(initiallyDark: boolean) {
  let matches = initiallyDark;
  const listeners = new Set<() => void>();
  const query = {
    get matches() {
      return matches;
    },
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: vi.fn((_event: string, listener: () => void) => {
      listeners.add(listener);
    }),
    removeEventListener: vi.fn((_event: string, listener: () => void) => {
      listeners.delete(listener);
    }),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } satisfies MediaQueryList;

  const matchMedia = vi.fn(() => query);
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: matchMedia,
  });

  return {
    matchMedia,
    query,
    setDark(next: boolean) {
      matches = next;
      for (const listener of listeners) listener();
    },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(window, "matchMedia");
});

describe("useSystemDarkMode", () => {
  it("tracks the operating-system color scheme and unsubscribes on cleanup", () => {
    const media = installMatchMedia(false);
    const { result, unmount } = renderHook(() => useSystemDarkMode());

    expect(result.current).toBe(false);
    expect(media.matchMedia).toHaveBeenCalledWith(
      "(prefers-color-scheme: dark)",
    );

    act(() => media.setDark(true));
    expect(result.current).toBe(true);

    unmount();
    expect(media.query.removeEventListener).toHaveBeenCalledWith(
      "change",
      expect.any(Function),
    );
  });
});
