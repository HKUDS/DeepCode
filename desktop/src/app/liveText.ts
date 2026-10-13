import type { Item, ItemDelta } from "../generated/app-server";
import {
  appendReasoningDelta,
  decodeReasoningPayload,
} from "./reasoningPayload";

/** Streamed text arrives as live `item.delta` notifications, never stored.
 * Offsets count UTF-8 bytes of one channel's text, as the service does. A
 * client keeps that count per in-progress item and channel; a delta past it
 * means one was missed, and `turn/read` has the text so far.
 */
export type LiveChannel = ItemDelta["channel"];

const encoder = new TextEncoder();
const decoder = new TextDecoder();

export function byteLength(text: string): number {
  return encoder.encode(text).length;
}

export function liveKey(itemId: string, channel: LiveChannel): string {
  return `${itemId}:${channel}`;
}

function channelsOf(item: Item): LiveChannel[] {
  if (item.kind === "assistant_message") return ["text"];
  if (item.kind === "reasoning_summary") return ["summary", "provider_trace"];
  return [];
}

export function streamsText(item: Item): boolean {
  return channelsOf(item).length > 0;
}

export function channelText(item: Item, channel: LiveChannel): string {
  if (channel === "text") {
    return typeof item.payload.text === "string" ? item.payload.text : "";
  }
  const reasoning = decodeReasoningPayload(item.payload);
  return channel === "summary" ? reasoning.summaryText : reasoning.traceText;
}

/** Byte counts of a streaming item's channels, keyed by `liveKey`. */
export function streamedBytes(item: Item): Record<string, number> {
  return Object.fromEntries(
    channelsOf(item).map((channel) => [
      liveKey(item.id, channel),
      byteLength(channelText(item, channel)),
    ]),
  );
}

export function withoutItem(
  bytes: Record<string, number>,
  itemId: string,
): Record<string, number> {
  const prefix = `${itemId}:`;
  if (!Object.keys(bytes).some((key) => key.startsWith(prefix))) return bytes;
  return Object.fromEntries(
    Object.entries(bytes).filter(([key]) => !key.startsWith(prefix)),
  );
}

function withChannelText(item: Item, channel: LiveChannel, text: string): Item {
  if (channel === "text") {
    return {
      ...item,
      summary: text.slice(0, 160),
      payload: { ...item.payload, text, streaming: true },
    };
  }
  return {
    ...item,
    payload: {
      ...appendReasoningDelta(item.payload, channel, ""),
      [channel === "summary" ? "summaryText" : "traceText"]: text,
    },
  };
}

/** The item with `delta` applied: unchanged if already held, `null` if a
 * delta was missed before it. `known` is the channel's byte count. */
export function applyLiveDelta(
  item: Item,
  known: number,
  delta: ItemDelta,
): { item: Item; known: number } | null {
  if (delta.offset > known) return null;
  if (delta.nextOffset <= known) return { item, known };
  const unseen = decoder.decode(
    encoder.encode(delta.delta).subarray(known - delta.offset),
  );
  return {
    item: withChannelText(
      item,
      delta.channel,
      channelText(item, delta.channel) + unseen,
    ),
    known: delta.nextOffset,
  };
}

/** Adopt the text a read returned where it is ahead of what streamed. */
export function adoptLiveText(
  item: Item,
  read: Item,
  bytes: Record<string, number>,
): { item: Item; bytes: Record<string, number> } {
  let next = item;
  const counts = { ...bytes };
  for (const channel of channelsOf(item)) {
    const text = channelText(read, channel);
    const size = byteLength(text);
    const key = liveKey(item.id, channel);
    if (size > (counts[key] ?? byteLength(channelText(item, channel)))) {
      next = withChannelText(next, channel, text);
      counts[key] = size;
    }
  }
  return { item: next, bytes: counts };
}
