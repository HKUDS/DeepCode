import { describe, expect, it } from "vitest";

import {
  automationIntervalInput,
  automationIntervalLabel,
  automationIntervalSeconds,
  MAX_AUTOMATION_INTERVAL_SECONDS,
} from "./automationInterval";

describe("automation intervals", () => {
  it.each([
    [7_200, { value: "2", unit: "hours" }],
    [900, { value: "15", unit: "minutes" }],
    [90, { value: "90", unit: "seconds" }],
  ] as const)(
    "represents %i seconds in the largest exact friendly unit",
    (intervalSeconds, expected) => {
      expect(automationIntervalInput(intervalSeconds)).toEqual(expected);
      expect(automationIntervalSeconds(expected)).toEqual({
        intervalSeconds,
        error: null,
      });
    },
  );

  it("formats exact interval labels without fractional conversions", () => {
    expect(automationIntervalLabel(3_600)).toBe("1 hour");
    expect(automationIntervalLabel(900)).toBe("15 minutes");
    expect(automationIntervalLabel(90)).toBe("90 seconds");
  });

  it.each([
    [
      { value: "1.5", unit: "minutes" as const },
      "Interval value must be a positive whole number.",
    ],
    [
      { value: "59", unit: "seconds" as const },
      "Interval must be at least 60 seconds.",
    ],
    [
      {
        value: String(MAX_AUTOMATION_INTERVAL_SECONDS + 1),
        unit: "seconds" as const,
      },
      "Interval must not exceed 366 days.",
    ],
    [
      { value: String(Number.MAX_SAFE_INTEGER + 1), unit: "hours" as const },
      "Interval is too large.",
    ],
  ])("rejects invalid protocol interval %#", (input, error) => {
    expect(automationIntervalSeconds(input)).toEqual({
      intervalSeconds: null,
      error,
    });
  });
});
