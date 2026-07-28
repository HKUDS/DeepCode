import { describe, expect, it } from "vitest";

import { nextTranscriptMode } from "./transcriptMode";

describe("transcript mode", () => {
  it("cycles through the same three product modes on every surface", () => {
    expect(nextTranscriptMode("normal")).toBe("verbose");
    expect(nextTranscriptMode("verbose")).toBe("summary");
    expect(nextTranscriptMode("summary")).toBe("normal");
  });
});
