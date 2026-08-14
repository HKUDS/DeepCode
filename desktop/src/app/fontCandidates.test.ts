import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendFamily,
  availableFontCandidates,
  FONT_CANDIDATES,
  isFontAvailable,
} from "./fontCandidates";

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "fonts");
});

function stubFonts(installed: string[]): void {
  Object.defineProperty(document, "fonts", {
    configurable: true,
    value: {
      check: (font: string) =>
        installed.some((family) => font.includes(`"${family}"`)),
    },
  });
}

describe("isFontAvailable", () => {
  it("reports nothing when the Font Loading API is absent", () => {
    // jsdom and older WebViews have no document.fonts. Claiming every family
    // exists there would offer the user settings that do nothing.
    Reflect.deleteProperty(document, "fonts");
    expect(isFontAvailable("Inter")).toBe(false);
  });

  it("asks the document rather than guessing", () => {
    stubFonts(["Inter"]);
    expect(isFontAvailable("Inter")).toBe(true);
    expect(isFontAvailable("Definitely Not Installed")).toBe(false);
  });

  it("survives a family name that would break the shorthand", () => {
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: {
        check: () => {
          throw new SyntaxError("bad font shorthand");
        },
      },
    });
    expect(isFontAvailable('bro"ken')).toBe(false);
  });
});

describe("availableFontCandidates", () => {
  it("offers only what is installed", () => {
    stubFonts(["Inter", "PingFang SC"]);
    expect(availableFontCandidates().map((c) => c.family)).toEqual([
      "Inter",
      "PingFang SC",
    ]);
  });

  it("returns nothing rather than the whole list when probing is impossible", () => {
    Reflect.deleteProperty(document, "fonts");
    expect(availableFontCandidates()).toEqual([]);
  });

  it("covers each group so the picker is useful on any platform", () => {
    const groups = new Set(FONT_CANDIDATES.map((c) => c.group));
    expect(groups).toEqual(new Set(["Interface", "Monospace", "CJK"]));
  });
});

describe("appendFamily", () => {
  it("adds to an empty list", () => {
    expect(appendFamily("", "Inter")).toBe("Inter");
  });

  it("appends without disturbing existing entries", () => {
    expect(appendFamily("Inter", "PingFang SC")).toBe("Inter, PingFang SC");
  });

  it("ignores a family already present, whatever its case", () => {
    expect(appendFamily("Inter, PingFang SC", "inter")).toBe(
      "Inter, PingFang SC",
    );
  });

  it("tidies stray separators from hand-typed input", () => {
    expect(appendFamily("Inter,  , ", "Roboto")).toBe("Inter, Roboto");
  });
});
