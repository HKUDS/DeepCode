import { beforeEach, describe, expect, it } from "vitest";

import {
  APPEARANCE_DEFAULTS,
  APPEARANCE_SETTINGS,
  applyAppearance,
  readAppearance,
  sanitizeAppearance,
  writeAppearance,
} from "./appearance";
import { parseVsCodeTheme } from "./importedTheme";

function root(): HTMLElement {
  return document.documentElement;
}

beforeEach(() => {
  localStorage.clear();
  root().removeAttribute("style");
  root().removeAttribute("data-theme");
});

describe("sanitizeAppearance", () => {
  it("fills every setting from an empty blob", () => {
    expect(sanitizeAppearance({})).toEqual(APPEARANCE_DEFAULTS);
  });

  it("clamps numbers into their declared range instead of rejecting them", () => {
    const state = sanitizeAppearance({ conversationWidth: 5000, fontSize: -3 });
    expect(state.conversationWidth).toBe(100);
    expect(state.fontSize).toBe(11);
  });

  it("survives values of the wrong type", () => {
    const state = sanitizeAppearance({
      conversationWidth: "not a number",
      theme: "chartreuse",
      fontFamily: 42,
    });
    expect(state).toEqual(APPEARANCE_DEFAULTS);
  });

  it("does not select an imported theme without a valid stored palette", () => {
    expect(sanitizeAppearance({ theme: "imported" }).theme).toBe("system");
  });

  it("is total over the settings table", () => {
    // A row added without a matching default would silently produce
    // `undefined` here rather than failing at the point of the mistake.
    const state = sanitizeAppearance({});
    for (const setting of APPEARANCE_SETTINGS) {
      expect(state[setting.key]).toBeDefined();
    }
  });
});

describe("persistence", () => {
  it("round-trips through storage", () => {
    writeAppearance({ ...APPEARANCE_DEFAULTS, fontSize: 18, theme: "dark" });
    const restored = readAppearance();
    expect(restored.fontSize).toBe(18);
    expect(restored.theme).toBe("dark");
  });

  it("round-trips a normalized imported palette", () => {
    const importedTheme = parseVsCodeTheme(
      '{"name":"Stored","colors":{"editor.background":"#123456"}}',
      "stored.json",
    );
    writeAppearance({
      ...APPEARANCE_DEFAULTS,
      theme: "imported",
      importedTheme,
    });
    const restored = readAppearance();
    expect(restored.theme).toBe("imported");
    expect(restored.importedTheme?.name).toBe("Stored");
    expect(restored.importedTheme?.tokens["--surface-canvas"]).toBe("#123456");
  });

  it("falls back to defaults when storage holds garbage", () => {
    localStorage.setItem("deepcode.desktop.appearance.v1", "{not json");
    expect(readAppearance()).toEqual(APPEARANCE_DEFAULTS);
  });
});

describe("applyAppearance", () => {
  it("writes nothing while every setting is at its default", () => {
    applyAppearance(APPEARANCE_DEFAULTS, root());
    // Defaults live in the stylesheet. Echoing them as inline properties
    // would shadow any future change to tokens.css.
    expect(root().getAttribute("style")).toBeFalsy();
    expect(root().hasAttribute("data-theme")).toBe(false);
  });

  it("exposes a narrowed conversation as a percentage", () => {
    applyAppearance({ ...APPEARANCE_DEFAULTS, conversationWidth: 70 }, root());
    expect(root().style.getPropertyValue("--conversation-width")).toBe("70%");
  });

  it("keeps the built-in cap at full width", () => {
    applyAppearance({ ...APPEARANCE_DEFAULTS, conversationWidth: 100 }, root());
    expect(root().style.getPropertyValue("--conversation-width")).toBe("");
  });

  it("pins the theme with an attribute a stylesheet can select on", () => {
    applyAppearance({ ...APPEARANCE_DEFAULTS, theme: "dark" }, root());
    expect(root().getAttribute("data-theme")).toBe("dark");

    applyAppearance({ ...APPEARANCE_DEFAULTS, theme: "system" }, root());
    expect(root().hasAttribute("data-theme")).toBe(false);
  });

  it("applies an imported palette through the theme attribute", () => {
    const importedTheme = parseVsCodeTheme(
      '{"colors":{"editor.background":"#123456"}}',
      "imported.json",
    );
    applyAppearance(
      { ...APPEARANCE_DEFAULTS, theme: "imported", importedTheme },
      root(),
    );
    expect(root().getAttribute("data-theme")).toBe("imported");
    expect(root().style.getPropertyValue("--surface-canvas")).toBe("#123456");

    applyAppearance(APPEARANCE_DEFAULTS, root());
    expect(root().style.getPropertyValue("--surface-canvas")).toBe("");
  });

  it("appends preferred fonts as a prefix of the built-in stack", () => {
    applyAppearance(
      { ...APPEARANCE_DEFAULTS, fontFamily: "Sarasa Mono SC, Inter" },
      root(),
    );
    // The trailing comma is what lets the default families follow, so an
    // unavailable font degrades instead of leaving the UI unstyled.
    expect(root().style.getPropertyValue("--font-ui-preferred")).toBe(
      "Sarasa Mono SC, Inter,",
    );
  });

  it("clears a preference when it returns to its default", () => {
    applyAppearance({ ...APPEARANCE_DEFAULTS, fontSize: 20 }, root());
    expect(root().style.getPropertyValue("--font-size-base")).toBe("20px");

    applyAppearance(APPEARANCE_DEFAULTS, root());
    expect(root().style.getPropertyValue("--font-size-base")).toBe("");
  });
});

describe("monospace font preference", () => {
  it("reaches the monospace stack, not the interface one", () => {
    applyAppearance(
      {
        ...APPEARANCE_DEFAULTS,
        fontFamily: "Inter",
        fontFamilyMono: "Cascadia Code",
      },
      root(),
    );
    expect(root().style.getPropertyValue("--font-ui-preferred")).toBe("Inter,");
    expect(root().style.getPropertyValue("--font-mono-preferred")).toBe(
      "Cascadia Code,",
    );
  });

  it("leaves the other list's variable untouched", () => {
    // Both lists are edited through the same control, so a picker wired to the
    // wrong key would quietly push a monospace face into the UI stack.
    applyAppearance(
      { ...APPEARANCE_DEFAULTS, fontFamilyMono: "JetBrains Mono" },
      root(),
    );
    expect(root().style.getPropertyValue("--font-ui-preferred")).toBe("");
  });

  it("falls back to the default for a list of the wrong type", () => {
    expect(sanitizeAppearance({ fontFamilyMono: 42 }).fontFamilyMono).toBe("");
  });

  it("carries each list's field copy on its own table row", () => {
    // The picker reads its placeholder and its note from the row it was reached
    // through. Copy that moved back into the renderer, or a row that lost it,
    // would render an unlabelled control instead of failing anywhere.
    const rows = APPEARANCE_SETTINGS.filter((setting) => setting.noteKey);
    expect(rows.map((setting) => setting.key)).toEqual([
      "fontFamily",
      "fontFamilyMono",
    ]);
    for (const row of rows) {
      expect(row.placeholder?.key).toMatch(/^settings\.appearance\.font/);
      expect(row.placeholder?.fallback).toBeTruthy();
    }
  });
});
