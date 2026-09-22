import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { __resetAppearanceStoreForTests } from "../../app/useAppearance";
import { ConversationSplitter } from "./ConversationSplitter";
import { SPLITTER_MAX, SPLITTER_MIN } from "./conversationWidthResize";

const STORAGE_KEY = "deepcode.desktop.appearance.v1";
const WORKSPACE_WIDTH = 1000;
const COLUMN_WIDTH = 700;
const HANDLE_WIDTH = 12;
/** Mirrors the stylesheet: the strip is centred on the column's right edge. */
const EDGE = WORKSPACE_WIDTH / 2 + COLUMN_WIDTH / 2;

function box(left: number, width: number): DOMRect {
  return {
    left,
    width,
    top: 0,
    right: left + width,
    bottom: 0,
    height: 0,
    x: left,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect;
}

function storedWidth(): number | undefined {
  const raw = localStorage.getItem(STORAGE_KEY);
  return raw === null ? undefined : JSON.parse(raw).conversationWidth;
}

function cssWidth(): string {
  return document.documentElement.style.getPropertyValue("--conversation-width");
}

/**
 * The splitter measures its parent, so it is rendered inside a stand-in
 * workspace whose geometry jsdom would otherwise report as all zeroes.
 */
function mount() {
  const view = render(
    <div data-testid="workspace">
      <ConversationSplitter />
    </div>,
  );
  const workspace = view.getByTestId("workspace");
  const handle = view.getByRole("separator");
  workspace.getBoundingClientRect = () => box(0, WORKSPACE_WIDTH);
  handle.getBoundingClientRect = () => box(EDGE - HANDLE_WIDTH / 2, HANDLE_WIDTH);
  return { handle, unmount: view.unmount };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("style");
  __resetAppearanceStoreForTests();
});

afterEach(() => {
  cleanup();
});

describe("ConversationSplitter", () => {
  it("previews while dragging and writes the share once, on release", () => {
    const { handle } = mount();

    fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: EDGE });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: EDGE + 40 });

    // 700px column + 2 * 40px = 780px of a 1000px workspace.
    expect(cssWidth()).toBe("78%");
    expect(storedWidth()).toBeUndefined();

    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(storedWidth()).toBe(78);
  });

  it("only moves the width while previewing, not the rest of the appearance", () => {
    const { handle } = mount();

    fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: EDGE });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: EDGE + 40 });

    // A preview repaints one custom property: no theme attribute, no font stack.
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
    expect(document.documentElement.style.getPropertyValue("--font-size-base")).toBe("");
    expect(cssWidth()).toBe("78%");
  });

  it("leaves the preference and the stylesheet alone when the press does not move", () => {
    const { handle } = mount();

    fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: EDGE });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: EDGE });

    // The default is measured as 70% here; committing it on a stray click would
    // silently narrow the column.
    expect(storedWidth()).toBeUndefined();
    expect(cssWidth()).toBe("");
  });

  it("puts the stored width back when it unmounts mid-drag", () => {
    const { handle, unmount } = mount();

    fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: EDGE });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: EDGE + 40 });
    expect(cssWidth()).toBe("78%");

    // Switching threads with the pointer still down must not leave the preview
    // painted over the stored preference.
    unmount();
    expect(cssWidth()).toBe("");
    expect(storedWidth()).toBeUndefined();
  });

  it("ignores pointer moves that belong to another pointer", () => {
    const { handle } = mount();

    fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: EDGE });
    fireEvent.pointerMove(window, { pointerId: 2, clientX: EDGE + 40 });
    fireEvent.pointerUp(window, { pointerId: 1 });

    expect(storedWidth()).toBeUndefined();
  });

  it("steps with the arrow keys and jumps to the ends of the range", () => {
    const { handle } = mount();

    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(storedWidth()).toBe(65);

    fireEvent.keyDown(handle, { key: "End" });
    expect(storedWidth()).toBe(SPLITTER_MAX);

    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(storedWidth()).toBe(SPLITTER_MAX);

    fireEvent.keyDown(handle, { key: "Home" });
    expect(storedWidth()).toBe(SPLITTER_MIN);

    fireEvent.keyDown(handle, { key: "Tab" });
    expect(storedWidth()).toBe(SPLITTER_MIN);
  });

  it("reveals the share it manages once one is stored", () => {
    const { handle } = mount();

    // At the default the handle announces no number rather than the cap.
    expect(handle.getAttribute("aria-valuenow")).toBeNull();

    fireEvent.keyDown(handle, { key: "End" });
    expect(handle.getAttribute("aria-valuenow")).toBe(String(SPLITTER_MAX));
    expect(handle.getAttribute("aria-valuetext")).toBe(`${SPLITTER_MAX}%`);
  });

  it("resets to the default width on double click", () => {
    const { handle } = mount();

    fireEvent.keyDown(handle, { key: "End" });
    expect(cssWidth()).toBe(`${SPLITTER_MAX}%`);

    fireEvent.doubleClick(handle);
    expect(storedWidth()).toBe(100);
    // 100 is the built-in cap, so the preference drops its override again.
    expect(cssWidth()).toBe("");
  });
});
