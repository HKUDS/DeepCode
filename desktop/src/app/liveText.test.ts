import { describe, expect, it } from "vitest";

import type { Item, ItemDelta } from "../generated/app-server";
import { adoptLiveText, applyLiveDelta, liveKey, streamedBytes } from "./liveText";

const bytes = (text: string) => new TextEncoder().encode(text).length;

function item(kind: Item["kind"], payload: Item["payload"]): Item {
  return {
    id: "item-1",
    threadId: "thread-1",
    turnId: "turn-1",
    ordinal: 1,
    kind,
    status: "in_progress",
    summary: "",
    payload,
    createdAt: "2026-10-13T00:00:00Z",
    updatedAt: "2026-10-13T00:00:00Z",
  };
}

function delta(
  channel: ItemDelta["channel"],
  before: string,
  text: string,
): ItemDelta {
  return {
    threadId: "thread-1",
    turnId: "turn-1",
    itemId: "item-1",
    channel,
    offset: bytes(before),
    nextOffset: bytes(before) + bytes(text),
    delta: text,
  };
}

describe("live streamed text", () => {
  it("appends at its offset, keeps only unseen bytes of an overlap, and stops at a gap", () => {
    const start = item("assistant_message", { text: "你好" });
    const known = bytes("你好");

    const next = applyLiveDelta(start, known, delta("text", "你好", "😀 world"))!;
    expect(next.item.payload.text).toBe("你好😀 world");
    expect(next.known).toBe(bytes("你好😀 world"));
    expect(next.item.summary).toBe("你好😀 world");

    // Re-sent from an earlier offset: only " world!" is new, cut at a whole
    // character even though "😀" takes four bytes.
    const overlap = applyLiveDelta(
      next.item,
      next.known,
      delta("text", "你好😀", " world!"),
    )!;
    expect(overlap.item.payload.text).toBe("你好😀 world!");

    expect(applyLiveDelta(next.item, next.known, delta("text", "你好", "😀"))).toEqual(
      next,
    );
    expect(applyLiveDelta(start, known, delta("text", "你好😀", " x"))).toBeNull();
  });

  it("streams reasoning channels independently and adopts a longer read", () => {
    const start = item("reasoning_summary", {
      summaryText: "plan",
      traceText: "",
      streaming: true,
    });
    expect(streamedBytes(start)).toEqual({
      [liveKey("item-1", "summary")]: 4,
      [liveKey("item-1", "provider_trace")]: 0,
    });
    const traced = applyLiveDelta(start, 0, delta("provider_trace", "", "trace"))!;
    expect(traced.item.payload).toMatchObject({
      summaryText: "plan",
      traceText: "trace",
      streaming: true,
    });

    const read = item("reasoning_summary", {
      summaryText: "plan more",
      traceText: "tr",
    });
    const adopted = adoptLiveText(traced.item, read, {
      [liveKey("item-1", "summary")]: 4,
      [liveKey("item-1", "provider_trace")]: 5,
    });
    // The summary read is ahead; the trace read is behind what streamed.
    expect(adopted.item.payload).toMatchObject({
      summaryText: "plan more",
      traceText: "trace",
    });
    expect(adopted.bytes[liveKey("item-1", "summary")]).toBe(9);
  });
});
