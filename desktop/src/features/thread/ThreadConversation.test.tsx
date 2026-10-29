import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Item, Turn } from "../../generated/app-server";
import { ThreadConversation } from "./ThreadConversation";

const turn: Turn = {
  id: "turn-1",
  threadId: "thread-1",
  ordinal: 1,
  prompt: "Inspect and repair",
  status: "running",
  stopReason: null,
  errorCode: null,
  errorMessage: null,
  startedAt: "2026-07-23T01:00:00Z",
  completedAt: null,
};

function item(
  id: string,
  ordinal: number,
  text: string,
  status: Item["status"] = "completed",
): Item {
  return {
    id,
    threadId: turn.threadId,
    turnId: turn.id,
    ordinal,
    kind: ordinal === 1 ? "user_message" : "assistant_message",
    status,
    summary: text,
    payload: { text },
    createdAt: "2026-07-23T01:00:01Z",
    updatedAt: `2026-07-23T01:00:0${ordinal}Z`,
  };
}

function conversation(turns: Turn[], items: Item[]) {
  return (
    <ThreadConversation
      turns={turns}
      items={items}
      approvals={[]}
      plansByTurnId={{}}
      selectedItemId={null}
      transcriptMode="normal"
      busy={false}
      onSelectItem={vi.fn()}
      onOpenInspector={vi.fn()}
      onRespondToApproval={vi.fn()}
      onRetryTurn={vi.fn()}
      onCancelQueuedTurn={vi.fn()}
    />
  );
}

/** jsdom has no layout: a viewport 500px tall over a transcript of `height`. */
function sizeViewport(viewport: HTMLElement, height: () => number) {
  Object.defineProperty(viewport, "scrollHeight", {
    configurable: true,
    get: height,
  });
  Object.defineProperty(viewport, "clientHeight", {
    configurable: true,
    value: 500,
  });
}

function scrollTo(viewport: HTMLElement, scrollTop: number) {
  viewport.scrollTop = scrollTop;
  fireEvent.scroll(viewport);
}

/** Stands in for the browser telling the view that the transcript resized. */
class FakeResizeObserver {
  static observers: FakeResizeObserver[] = [];

  constructor(private readonly callback: ResizeObserverCallback) {
    FakeResizeObserver.observers.push(this);
  }

  observe() {}

  disconnect() {
    FakeResizeObserver.observers = FakeResizeObserver.observers.filter(
      (observer) => observer !== this,
    );
  }

  static resize() {
    for (const observer of FakeResizeObserver.observers) {
      observer.callback([], observer as unknown as ResizeObserver);
    }
  }
}

describe("ThreadConversation", () => {
  let height = 2000;
  const prompt = item("user", 1, turn.prompt);

  /** Opens a Session whose history arrives after it opens, as switching does. */
  function openSession() {
    const { rerender } = render(conversation([], []));
    rerender(
      conversation([turn], [prompt, item("reply", 2, "Working", "in_progress")]),
    );
    const viewport = screen.getByLabelText("Thread conversation");
    sizeViewport(viewport, () => height);
    // The history lays out after it arrives.
    FakeResizeObserver.resize();
    return viewport;
  }

  beforeEach(() => {
    height = 2000;
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    FakeResizeObserver.observers = [];
  });

  it("opens at the latest and stays there as the transcript grows", () => {
    const viewport = openSession();
    expect(viewport.scrollTop).toBe(2000);

    height = 2600;
    FakeResizeObserver.resize();
    expect(viewport.scrollTop).toBe(2600);
    expect(screen.queryByRole("button", { name: "Latest" })).toBeNull();
  });

  it("stays put once the user scrolls up, until they return to the latest", () => {
    const viewport = openSession();
    scrollTo(viewport, 1000);
    expect(screen.getByRole("button", { name: "Latest" })).toBeTruthy();

    height = 2600;
    FakeResizeObserver.resize();
    expect(viewport.scrollTop).toBe(1000);

    fireEvent.click(screen.getByRole("button", { name: "Latest" }));
    expect(viewport.scrollTop).toBe(2600);
    expect(screen.queryByRole("button", { name: "Latest" })).toBeNull();

    height = 3000;
    FakeResizeObserver.resize();
    expect(viewport.scrollTop).toBe(3000);
  });

  it("keeps following after a small scroll near the end", () => {
    const viewport = openSession();
    scrollTo(viewport, 2000 - 500 - 30);
    expect(screen.queryByRole("button", { name: "Latest" })).toBeNull();

    height = 2400;
    FakeResizeObserver.resize();
    expect(viewport.scrollTop).toBe(2400);
  });

  it("follows again once the user scrolls back to the end", () => {
    const viewport = openSession();
    scrollTo(viewport, 600);
    scrollTo(viewport, 1500);
    expect(screen.queryByRole("button", { name: "Latest" })).toBeNull();

    height = 2200;
    FakeResizeObserver.resize();
    expect(viewport.scrollTop).toBe(2200);
  });
});
