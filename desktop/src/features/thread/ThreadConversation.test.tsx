import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

function placeViewport(viewport: HTMLElement, scrollTop: number) {
  Object.defineProperty(viewport, "scrollHeight", {
    configurable: true,
    value: 2000,
  });
  Object.defineProperty(viewport, "clientHeight", {
    configurable: true,
    value: 500,
  });
  viewport.scrollTop = scrollTop;
  fireEvent.scroll(viewport);
}

describe("ThreadConversation", () => {
  afterEach(() => {
    cleanup();
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollTo;
  });

  it("tracks scrolling in a Session whose history arrives after it opens", () => {
    const scrollTo = vi.fn();
    HTMLElement.prototype.scrollTo = scrollTo as HTMLElement["scrollTo"];
    // Switching threads clears the conversation before the history is read.
    const { rerender } = render(conversation([], []));
    const prompt = item("user", 1, turn.prompt);
    rerender(
      conversation([turn], [prompt, item("reply", 2, "Working", "in_progress")]),
    );
    // Opens at the bottom at once rather than smooth-scrolling from the top.
    expect(scrollTo).toHaveBeenNthCalledWith(1, {
      top: expect.any(Number),
      behavior: "auto",
    });

    const viewport = screen.getByLabelText("Thread conversation");
    placeViewport(viewport, 200);
    scrollTo.mockClear();

    expect(screen.getByRole("button", { name: "Latest" })).toBeTruthy();
    rerender(conversation([turn], [prompt, item("reply", 2, "Still working")]));
    expect(scrollTo).not.toHaveBeenCalled();

    placeViewport(viewport, 1500);
    expect(screen.queryByRole("button", { name: "Latest" })).toBeNull();
    rerender(
      conversation(
        [turn],
        [prompt, item("reply", 2, "Still working"), item("done", 3, "Done")],
      ),
    );
    expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: "auto" });
  });
});
