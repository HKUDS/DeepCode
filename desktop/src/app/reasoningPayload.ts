import type { Item } from "../generated/app-server";

export type ReasoningChannel = "summary" | "provider_trace";
export type ReasoningAvailability = "available" | "opaque";

export interface ReasoningPayloadView {
  schemaVersion: number;
  summaryText: string;
  traceText: string;
  availability: ReasoningAvailability;
  effort: string | null;
  durationMs: number | null;
  streaming: boolean;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function nonNegativeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

export function decodeReasoningPayload(
  payload: Item["payload"],
): ReasoningPayloadView {
  const summaryText =
    stringValue(payload.summaryText) || stringValue(payload.text);
  const traceText = stringValue(payload.traceText);
  return {
    schemaVersion:
      typeof payload.schemaVersion === "number" &&
      Number.isInteger(payload.schemaVersion)
        ? payload.schemaVersion
        : 0,
    summaryText,
    traceText,
    availability:
      payload.availability === "opaque" && !summaryText && !traceText
        ? "opaque"
        : "available",
    effort: optionalString(payload.effort),
    durationMs: nonNegativeNumber(payload.durationMs),
    streaming: payload.streaming === true,
  };
}

export function appendReasoningDelta(
  payload: Item["payload"],
  channel: ReasoningChannel,
  delta: string,
): Item["payload"] {
  const current = decodeReasoningPayload(payload);
  return {
    ...payload,
    schemaVersion: 1,
    summaryText:
      channel === "summary"
        ? current.summaryText + delta
        : current.summaryText,
    traceText:
      channel === "provider_trace"
        ? current.traceText + delta
        : current.traceText,
    availability: "available",
    streaming: true,
  };
}
