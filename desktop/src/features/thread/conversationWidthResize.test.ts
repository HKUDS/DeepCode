import { describe, expect, it } from "vitest";

import {
  measureColumnWidth,
  percentFromDrag,
  percentFromKey,
  projectPercent,
  SPLITTER_MAX,
  SPLITTER_MIN,
  SPLITTER_STEP,
} from "./conversationWidthResize";

describe("measureColumnWidth", () => {
  it("reads a centred column's full width off the handle's centre line", () => {
    const width = measureColumnWidth(
      { left: 0, width: 1000 },
      { left: 844, width: 12 },
    );
    expect(width).toBe(700);
  });

  it("never reports a negative width", () => {
    const width = measureColumnWidth(
      { left: 0, width: 1000 },
      { left: 100, width: 12 },
    );
    expect(width).toBe(0);
  });
});

describe("percentFromDrag", () => {
  it("moves the grabbed edge with the pointer", () => {
    // A centred column grows on both sides, so 40px of travel is 80px of width.
    expect(percentFromDrag(700, 1000, 40)).toBe(78);
  });

  it("clamps to the slider's minimum", () => {
    expect(percentFromDrag(700, 1000, -400)).toBe(SPLITTER_MIN);
  });

  it("stops below the legacy cap", () => {
    expect(percentFromDrag(700, 1000, 400)).toBe(SPLITTER_MAX);
  });

  it("stays usable when there is nothing to measure", () => {
    expect(percentFromDrag(0, 0, 10)).toBe(SPLITTER_MAX);
  });
});

describe("projectPercent", () => {
  it("passes a stored share through", () => {
    expect(projectPercent(70, 700, 1000)).toBe(70);
  });

  it("measures the default instead of trusting the cap", () => {
    expect(projectPercent(100, 700, 1000)).toBe(70);
  });

  it("keeps the measurement inside the scale", () => {
    expect(projectPercent(100, 1000, 1000)).toBe(SPLITTER_MAX);
  });

  it("falls back when there is nothing to measure", () => {
    expect(projectPercent(100, 0, 0)).toBe(SPLITTER_MAX);
  });
});

describe("percentFromKey", () => {
  it("steps by the slider's step", () => {
    expect(percentFromKey(70, "ArrowRight", SPLITTER_STEP)).toBe(75);
    expect(percentFromKey(70, "ArrowLeft", SPLITTER_STEP)).toBe(65);
  });

  it("runs to the ends of the range", () => {
    expect(percentFromKey(70, "Home", SPLITTER_STEP)).toBe(SPLITTER_MIN);
    expect(percentFromKey(70, "End", SPLITTER_STEP)).toBe(SPLITTER_MAX);
  });

  it("ignores keys it does not own", () => {
    expect(percentFromKey(70, "Tab", SPLITTER_STEP)).toBeNull();
    expect(percentFromKey(70, "Enter", SPLITTER_STEP)).toBeNull();
  });
});
