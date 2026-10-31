import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NARROW_LAYOUT } from "./layout";
import { useNavigationDrawer } from "./useNavigationDrawer";

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

describe("useNavigationDrawer focus and Escape", () => {
  function mountDrawer() {
    const drawer = document.createElement("aside");
    drawer.id = "nav";
    drawer.innerHTML = '<button type="button">New Session</button><button type="button">Skills</button>';
    const button = document.createElement("button");
    button.textContent = "Menu";
    document.body.append(drawer, button);
    return { drawer, button, remove: () => { drawer.remove(); button.remove(); } };
  }

  it("moves focus into the drawer and back to the menu button, never on load", () => {
    installWindowWidth(true);
    const dom = mountDrawer();
    const { result } = renderHook(() => useNavigationDrawer("nav"));
    result.current.buttonRef.current = dom.button;
    expect(document.activeElement).toBe(document.body);

    act(() => result.current.toggle());
    expect(document.activeElement?.textContent).toBe("New Session");

    act(() => result.current.close());
    expect(document.activeElement).toBe(dom.button);
    dom.remove();
  });

  it("closes on Escape, but an inner layer such as a row's menu closes first", async () => {
    const { useEscapeLayer } = await import("./escapeLayer");
    installWindowWidth(true);
    const dom = mountDrawer();
    const { result } = renderHook(() => useNavigationDrawer("nav"));
    act(() => result.current.toggle());
    const closeMenu = vi.fn();
    const menu = renderHook(() => useEscapeLayer(closeMenu, true));

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(closeMenu).toHaveBeenCalledOnce();
    expect(result.current.open).toBe(true);

    menu.unmount();
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(result.current.open).toBe(false);
    dom.remove();
  });
});
