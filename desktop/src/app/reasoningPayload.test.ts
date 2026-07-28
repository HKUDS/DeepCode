import { describe, expect, it } from "vitest";

import {
  appendReasoningDelta,
  decodeReasoningPayload,
} from "./reasoningPayload";

describe("reasoning payload", () => {
  it("decodes legacy summary payloads", () => {
    const decoded = decodeReasoningPayload({ text: "Legacy summary" });

    expect(decoded.summaryText).toBe("Legacy summary");
    expect(decoded.traceText).toBe("");
    expect(decoded.availability).toBe("available");
  });

  it("keeps opaque state truthful when no display text exists", () => {
    const decoded = decodeReasoningPayload({
      schemaVersion: 1,
      availability: "opaque",
    });

    expect(decoded.availability).toBe("opaque");
  });

  it("appends each typed channel independently", () => {
    const summary = appendReasoningDelta({}, "summary", "Checked.");
    const trace = appendReasoningDelta(
      summary,
      "provider_trace",
      "Provider detail.",
    );

    expect(decodeReasoningPayload(trace)).toMatchObject({
      summaryText: "Checked.",
      traceText: "Provider detail.",
      availability: "available",
      streaming: true,
    });
  });
});
