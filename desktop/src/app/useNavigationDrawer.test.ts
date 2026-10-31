import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NARROW_LAYOUT, useNavigationDrawer } from "./useNavigationDrawer";

function installWindowWidth(narrow: boolean) {
  let matches = narrow;
  const listeners = new Set<() => void>();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn((query: string) => ({
      get matches() {
        return query === NARROW_LAYOUT ? matches : false;
      },
      media: query,
      onchange: null,
      addEventListener: (_event: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_event: string, listener: () => void) => listeners.delete(listener),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  return {
    resize(next: boolean) {
      matches = next;
      for (const listener of listeners) listener();
    },
  };
}

afterEach(() => {
  Reflect.deleteProperty(window, "matchMedia");
});

describe("useNavigationDrawer", () => {
  it("never opens on a wide window, where the sidebar is a column", () => {
    installWindowWidth(false);
    const { result } = renderHook(() => useNavigationDrawer("nav"));

    act(() => result.current.toggle());

    expect(result.current.open).toBe(false);
  });

  it("opens on a narrow window and closes when a destination is chosen", () => {
    installWindowWidth(true);
    const { result } = renderHook(() => useNavigationDrawer("nav"));

    act(() => result.current.toggle());
    expect(result.current.open).toBe(true);

    act(() => result.current.close());
    expect(result.current.open).toBe(false);
  });

  it("closes itself when the window widens, so nothing stays hidden", () => {
    const width = installWindowWidth(true);
    const { result } = renderHook(() => useNavigationDrawer("nav"));
    act(() => result.current.toggle());

    act(() => width.resize(false));
    expect(result.current.open).toBe(false);
    // Narrow again later: the drawer does not reopen by itself.
    act(() => width.resize(true));
    expect(result.current.open).toBe(false);
  });
});
