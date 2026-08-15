import { describe, expect, it } from "vitest";

import { capacityText, parseCapacity } from "./modelCapacity";

describe("model capacity round-trip", () => {
  it("renders counts with the largest exact suffix", () => {
    expect(capacityText(131072)).toBe("131072");
    expect(capacityText(128000)).toBe("128K");
    expect(capacityText(1_000_000)).toBe("1M");
    expect(capacityText(null)).toBe("");
  });

  it("parses K/M suffixes case-insensitively (1M = 1000K)", () => {
    expect(parseCapacity("128K")).toBe(128000);
    expect(parseCapacity("1m")).toBe(1_000_000);
    expect(parseCapacity("0.5M")).toBe(500000);
    expect(parseCapacity("65536")).toBe(65536);
    expect(parseCapacity("  ")).toBeNull();
  });

  it("flags unparseable or non-positive text instead of guessing", () => {
    expect(parseCapacity("lots")).toBeNaN();
    expect(parseCapacity("12KB")).toBeNaN();
    expect(parseCapacity("0")).toBeNaN();
  });
});
