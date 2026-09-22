import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  appendFamily,
  availableFontCandidates,
  FONT_CANDIDATES,
  isFontAvailable,
} from "./fontCandidates";

/**
 * A stand-in for the engine's font matching, faithful to what Edge/WebView2
 * 153.0.4234 was measured doing: a `local()` lookup resolves for a family the
 * machine has, and rejects with NetworkError for one it does not.
 *
 * The previous suite replaced `document.fonts.check()` with a fake that encoded
 * the *assumption* it reports absence. That is how a picker which listed every
 * family on the machine, installed or not, stayed green.
 */
function stubFontMatching(
  installed: readonly string[],
  options: { refuse?: boolean } = {},
): string[] {
  const sources: string[] = [];
  class FakeFontFace {
    family = "";

    constructor(_name: string, source: string) {
      // An engine can refuse the lookup itself, e.g. a malformed source.
      if (options.refuse) throw new DOMException("refused", "SyntaxError");
      sources.push(source);
      this.family = /^local\("(.*)"\)$/.exec(source)?.[1] ?? "";
    }

    load(): Promise<FakeFontFace> {
      return installed.includes(this.family)
        ? Promise.resolve(this)
        : Promise.reject(
            new DOMException(`${this.family} is not available`, "NetworkError"),
          );
    }
  }
  vi.stubGlobal("FontFace", FakeFontFace);
  return sources;
}

/** The engine's own answer about availability, for the record: true for anything. */
function stubCheckAlwaysTrue(): Mock<() => boolean> {
  const check = vi.fn(() => true);
  Object.defineProperty(document, "fonts", {
    configurable: true,
    value: { check },
  });
  return check;
}

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "fonts");
});

describe("isFontAvailable", () => {
  it("does not consult document.fonts.check, which reports true for absent families", async () => {
    // Measured on Edge/WebView2 153.0.4234 — the engine Tauri uses on Windows —
    // where check() answered true for all 39 families in a sweep that included
    // `__Absent Font 12345__`: an unmatched family still renders through the
    // fallback. Asking it offers every candidate on every machine.
    const check = stubCheckAlwaysTrue();
    stubFontMatching(["Inter"]);

    expect(check()).toBe(true); // the engine's answer, for the record
    check.mockClear();

    expect(await isFontAvailable("__Absent Font 12345__")).toBe(false);
    expect(check).not.toHaveBeenCalled();
  });

  it("resolves a family the machine has", async () => {
    stubFontMatching(["Inter"]);

    expect(await isFontAvailable("Inter")).toBe(true);
  });

  it("asks the engine about the family it was given", async () => {
    const sources = stubFontMatching(["Inter"]);

    await isFontAvailable("Inter");

    expect(sources).toEqual(['local("Inter")']);
  });

  it("reports a family the machine lacks as unavailable", async () => {
    stubFontMatching(["Inter"]);

    expect(await isFontAvailable("Definitely Not Installed")).toBe(false);
  });

  it("survives a family name that would end the lookup string", async () => {
    const sources = stubFontMatching(["broken"]);

    expect(await isFontAvailable('bro"ken')).toBe(true);
    expect(sources).toEqual(['local("broken")']);
  });

  it("reports nothing when the engine has no FontFace", async () => {
    // jsdom, and a WebView without the Font Loading API: claim nothing rather
    // than offer the user settings that do nothing.
    vi.stubGlobal("FontFace", undefined);

    expect(await isFontAvailable("Inter")).toBe(false);
  });

  it("reports nothing when the engine refuses the lookup", async () => {
    stubFontMatching(["Inter"], { refuse: true });

    expect(await isFontAvailable("Inter")).toBe(false);
  });

  it("leaves document.fonts untouched", async () => {
    // Probing must not register anything: the face is loaded to ask a question,
    // not to be used for rendering.
    const add = vi.fn();
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { add },
    });
    stubFontMatching(["Inter"]);

    await isFontAvailable("Inter");

    expect(add).not.toHaveBeenCalled();
  });
});

describe("availableFontCandidates", () => {
  it("offers only what the machine has, in declaration order", async () => {
    stubFontMatching(["PingFang SC", "Consolas", "Inter"]);

    expect((await availableFontCandidates()).map((c) => c.family)).toEqual([
      "Inter",
      "Consolas",
      "PingFang SC",
    ]);
  });

  it("returns nothing rather than the whole list when probing is impossible", async () => {
    vi.stubGlobal("FontFace", undefined);

    expect(await availableFontCandidates()).toEqual([]);
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
