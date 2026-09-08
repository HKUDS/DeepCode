import { beforeEach, describe, expect, it } from "vitest";

import {
  IMPORTED_THEME_TOKEN_NAMES,
  ThemeImportError,
  applyImportedTheme,
  parseVsCodeTheme,
  sanitizeImportedTheme,
} from "./importedTheme";

const SOURCE = `{
  // VS Code themes commonly use JSONC.
  "name": "Night Test",
  "colors": {
    "editor.background": "#112233",
    "editor.foreground": "#ddeeff",
    "sideBar.background": "#223344",
    "focusBorder": "#abc",
    "testing.iconPassed": "#22aa66",
    "widget.shadow": "#000000",
  },
}`;

beforeEach(() => {
  document.documentElement.removeAttribute("style");
});

describe("parseVsCodeTheme", () => {
  it("accepts JSONC, infers a dark base, and fills every token", () => {
    const theme = parseVsCodeTheme(SOURCE, "night-color-theme.jsonc");

    expect(theme.name).toBe("Night Test");
    expect(theme.base).toBe("dark");
    expect(theme.tokens["--surface-canvas"]).toBe("#112233");
    expect(theme.tokens["--text-primary"]).toBe("#ddeeff");
    expect(theme.tokens["--surface-sidebar"]).toBe("#223344");
    expect(theme.tokens["--signal"]).toBe("#aabbcc");
    expect(theme.tokens["--signal-soft"]).toBe("#aabbcc29");
    expect(theme.tokens["--shadow-float"]).toContain("0 18px 48px #00000052");
    expect(Object.keys(theme.tokens).sort()).toEqual(
      [...IMPORTED_THEME_TOKEN_NAMES].sort(),
    );
  });

  it("rejects invalid colors and unsupported include chains visibly", () => {
    expect(() =>
      parseVsCodeTheme('{"colors":{"editor.background":"red"}}', "bad.json"),
    ).toThrow(ThemeImportError);
    expect(() =>
      parseVsCodeTheme(
        '{"include":"./base.json","colors":{"editor.background":"#fff"}}',
        "included.json",
      ),
    ).toThrow(/include chains are not supported/i);
  });

  it("derives a useful name from the file without persisting its path", () => {
    const theme = parseVsCodeTheme(
      '{"colors":{"editor.background":"#ffffff"}}',
      "Quiet-Light-color-theme.json",
    );
    expect(theme.name).toBe("Quiet-Light");
    expect(JSON.stringify(theme)).not.toContain("color-theme.json");
  });
});

describe("stored and applied themes", () => {
  it("sanitizes known tokens and restores a complete base", () => {
    const theme = sanitizeImportedTheme({
      name: "Stored",
      base: "light",
      tokens: { "--surface-canvas": "#abcdef" },
    });
    expect(theme?.tokens["--surface-canvas"]).toBe("#abcdef");
    expect(Object.keys(theme?.tokens ?? {})).toHaveLength(
      IMPORTED_THEME_TOKEN_NAMES.length,
    );
  });

  it("writes every imported token and clears them together", () => {
    const theme = parseVsCodeTheme(SOURCE, "night.jsonc");
    const root = document.documentElement;

    applyImportedTheme(theme, root);
    expect(root.style.getPropertyValue("--imported-color-scheme")).toBe("dark");
    expect(root.style.getPropertyValue("--surface-canvas")).toBe("#112233");

    applyImportedTheme(null, root);
    expect(root.style.getPropertyValue("--imported-color-scheme")).toBe("");
    expect(root.style.getPropertyValue("--surface-canvas")).toBe("");
  });
});
