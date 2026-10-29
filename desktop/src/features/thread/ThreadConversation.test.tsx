import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { initI18n } from "../../app/i18n";
import type { Item, Turn } from "../../generated/app-server";
import { ThreadConversation } from "./ThreadConversation";

initI18n();

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

const VIEW_HEIGHT = 500;

/**
 * jsdom has no layout: a 500px viewport over a transcript as tall as
 * `transcriptHeight()`, whose scroll position the "browser" clamps.
 */
function layOut(viewport: HTMLElement, transcriptHeight: () => number) {
  let top = 0;
  const maxTop = () => Math.max(0, transcriptHeight() - VIEW_HEIGHT);
  Object.defineProperties(viewport, {
    scrollHeight: { configurable: true, get: transcriptHeight },
    clientHeight: { configurable: true, value: VIEW_HEIGHT },
    scrollTop: {
      configurable: true,
      get: () => Math.min(top, maxTop()),
      set: (value: number) => {
        top = Math.max(0, Math.min(value, maxTop()));
      },
    },
  });
}

function distanceFromEnd(viewport: HTMLElement) {
  return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
}

function userScrollsTo(viewport: HTMLElement, scrollTop: number) {
  viewport.scrollTop = scrollTop;
  fireEvent.scroll(viewport);
}

function latestButton() {
  return screen.queryByRole("button", { name: "Latest" });
}

/** Stands in for the browser reporting that the transcript resized. */
class FakeResizeObserver implements ResizeObserver {
  static observers: FakeResizeObserver[] = [];

  constructor(private readonly callback: ResizeObserverCallback) {
    FakeResizeObserver.observers.push(this);
  }

  observe() {}

  unobserve() {}

  disconnect() {
    FakeResizeObserver.observers = FakeResizeObserver.observers.filter(
      (observer) => observer !== this,
    );
  }

  static resize() {
    act(() => {
      for (const observer of FakeResizeObserver.observers) {
        observer.callback([], observer);
      }
    });
  }
}

describe("ThreadConversation", () => {
  let height = 2000;
  const prompt = item("user", 1, turn.prompt);

  /** Grows or shrinks the transcript, as layout would, and reports it. */
  function resizeTo(next: number) {
    height = next;
    FakeResizeObserver.resize();
  }

  let showItems: (items: Item[]) => void = () => {};

  /** Opens a Session whose history arrives after it opens, as switching does. */
  function openSession(items: Item[] = [prompt, item("reply", 2, "Working", "in_progress")]) {
    const { rerender } = render(conversation([], []));
    showItems = (next) => rerender(conversation([turn], next));
    showItems(items);
    const viewport = screen.getByLabelText("Thread conversation");
    layOut(viewport, () => height);
    // The history lays out after it arrives.
    FakeResizeObserver.resize();
    return viewport;
  }

  /** A run of commands the running Turn is still at. */
  function commands(count: number): Item[] {
    return Array.from({ length: count }, (_, index) => ({
      ...item(`run-${index}`, index + 2, `command ${index}`),
      kind: "command_execution" as const,
      payload: { activity: { kind: "run", label: "Run", subject: `command ${index}` } },
    }));
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
    expect(distanceFromEnd(viewport)).toBe(0);

    resizeTo(2600);
    expect(distanceFromEnd(viewport)).toBe(0);
    expect(latestButton()).toBeNull();
  });

  it("keeps following when content lands before the pin's scroll event", () => {
    const viewport = openSession();
    resizeTo(2100);
    // More arrives, then the scroll event of the last pin is delivered.
    height = 2200;
    fireEvent.scroll(viewport);
    expect(latestButton()).toBeNull();

    FakeResizeObserver.resize();
    expect(distanceFromEnd(viewport)).toBe(0);
  });

  it("stays put once the user scrolls up, until they ask for the latest", () => {
    const viewport = openSession();
    userScrollsTo(viewport, 1000);
    expect(latestButton()).not.toBeNull();

    resizeTo(2600);
    expect(viewport.scrollTop).toBe(1000);

    fireEvent.click(latestButton()!);
    expect(distanceFromEnd(viewport)).toBe(0);
    expect(latestButton()).toBeNull();

    resizeTo(3000);
    expect(distanceFromEnd(viewport)).toBe(0);
  });

  it("notices the user leaving again after they scrolled back to the end", () => {
    const viewport = openSession();
    userScrollsTo(viewport, 600);
    resizeTo(5000);
    userScrollsTo(viewport, 5000 - VIEW_HEIGHT);
    expect(latestButton()).toBeNull();

    userScrollsTo(viewport, 5000 - VIEW_HEIGHT - 600);
    expect(latestButton()).not.toBeNull();
    resizeTo(5020);
    expect(viewport.scrollTop).toBe(5000 - VIEW_HEIGHT - 600);
  });

  it("keeps following after a small nudge near the end", () => {
    const viewport = openSession();
    userScrollsTo(viewport, 2000 - VIEW_HEIGHT - 30);
    expect(latestButton()).toBeNull();

    resizeTo(2400);
    expect(distanceFromEnd(viewport)).toBe(0);
  });

  it("follows again once the user scrolls back to the end", () => {
    const viewport = openSession();
    userScrollsTo(viewport, 600);
    userScrollsTo(viewport, 2000 - VIEW_HEIGHT);
    expect(latestButton()).toBeNull();

    resizeTo(2200);
    expect(distanceFromEnd(viewport)).toBe(0);
  });

  it("leaves an opened step where it is, and keeps following when one closes", () => {
    const instruction = "Continue working toward the active Thread Goal.";
    const goalPrompt: Item = {
      ...item("user", 1, instruction),
      payload: { text: instruction, source: "goal_continuation" },
    };
    const viewport = openSession([goalPrompt, item("reply", 2, "Working", "in_progress")]);
    const summary = screen.getByText("Working toward the Goal").closest("summary")!;
    const step = summary.closest("details")!;
    const before = viewport.scrollTop;

    fireEvent.click(summary);
    expect(step.open).toBe(true);
    resizeTo(3500);
    expect(viewport.scrollTop).toBe(before);
    expect(latestButton()).not.toBeNull();

    // Back at the end, closing the step pushes nothing away: still following.
    userScrollsTo(viewport, 3500 - VIEW_HEIGHT);
    fireEvent.click(summary);
    expect(step.open).toBe(false);
    expect(latestButton()).toBeNull();
    resizeTo(2000);
    expect(distanceFromEnd(viewport)).toBe(0);
  });

  it("stops following when the user reveals a run's earlier steps", () => {
    const viewport = openSession([prompt, ...commands(25)]);
    fireEvent.click(screen.getByRole("button", { name: "Show all 25 steps" }));
    expect(latestButton()).not.toBeNull();

    resizeTo(5000);
    expect(distanceFromEnd(viewport)).toBeGreaterThan(0);
  });

  it("holds a run open for a reader up in it, and folds it once they return", () => {
    const run = [prompt, ...commands(2)];
    const viewport = openSession(run);
    userScrollsTo(viewport, 600);

    showItems([...run, { ...item("note", 4, "Both commands pass"), payload: { text: "Both commands pass", phase: "commentary" } }]);
    expect(screen.getByText("Steps").closest("details")?.open).toBe(true);

    userScrollsTo(viewport, 2000 - VIEW_HEIGHT);
    expect(screen.getByText("Steps").closest("details")?.open).toBe(false);
  });
});
