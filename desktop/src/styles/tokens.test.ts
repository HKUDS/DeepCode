import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { THEME_PREFERENCES } from "../app/appearance";

// Vitest runs from the Vite root (desktop/); jsdom leaves import.meta.url as
// a non-file URL, so resolve against the project root instead.
// Comments are stripped first: the declaration regex below is not a CSS parser,
// so a `word:` inside prose swallows everything up to the next real semicolon —
// and with it the declaration that followed.
const CSS = readFileSync(resolve("src/styles/tokens.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

/** Declarations inside the first `{ … }` that follows `selector`. */
function declarationsAfter(selector: string): Map<string, string> {
  const start = CSS.indexOf(selector);
  if (start < 0) throw new Error(`selector not found: ${selector}`);

  let depth = 0;
  let bodyStart = -1;
  let index = start;
  for (; index < CSS.length; index += 1) {
    if (CSS[index] === "{") {
      depth += 1;
      if (depth === 1) bodyStart = index + 1;
    } else if (CSS[index] === "}") {
      depth -= 1;
      if (depth === 0) break;
    }
  }

  const body = CSS.slice(bodyStart, index);
  const declarations = new Map<string, string>();
  for (const [, property, value] of body.matchAll(
    /([\w-]+)\s*:\s*([^;]+);/g,
  )) {
    declarations.set(property.trim(), value.replace(/\s+/g, " ").trim());
  }
  return declarations;
}

describe("dark palette", () => {
  // CSS cannot share one declaration block between a media query and a
  // selector, so the dark values are written twice. Copies drift silently —
  // exactly the failure this project hit in its model catalog — so the copy
  // is pinned here rather than trusted.
  // The inner rule, not the @media wrapper: starting at the wrapper would
  // make the selector line itself look like a declaration.
  const fromMediaQuery = declarationsAfter(':root:not([data-theme="light"])');
  const fromAttribute = declarationsAfter(':root[data-theme="dark"]');

  it("declares the same properties in both places", () => {
    expect([...fromAttribute.keys()].sort()).toEqual(
      [...fromMediaQuery.keys()].sort(),
    );
  });

  it("gives every property the same value in both places", () => {
    expect(Object.fromEntries(fromAttribute)).toEqual(
      Object.fromEntries(fromMediaQuery),
    );
  });

  it("covers the palette rather than a token or two", () => {
    // A guard against the parser silently matching an empty block.
    expect(fromMediaQuery.size).toBeGreaterThan(25);
  });
});

describe("optional palettes", () => {
  // A theme that forgets a token does not fail loudly — the base :root value
  // shows through, so one component keeps a light surface inside a dark
  // palette and nothing anywhere reports it. Dark is the reference set because
  // it overrides exactly the palette and nothing else.
  const reference = [...declarationsAfter(':root[data-theme="dark"]').keys()];

  // Driven off the real list, so adding a name to THEME_PREFERENCES without
  // writing its palette fails here rather than shipping a half-themed app.
  // Two names are exempt: "system" sets no attribute at all, and "light" is
  // the base :root, so its block carries only `color-scheme`.
  const palettes = THEME_PREFERENCES.filter(
    (theme) => theme !== "system" && theme !== "light",
  );

  it("checks every theme the picker offers", () => {
    expect(palettes.length).toBeGreaterThanOrEqual(3);
  });

  it.each(palettes)("%s declares the whole palette", (theme) => {
    const declared = [
      ...declarationsAfter(`:root[data-theme="${theme}"]`).keys(),
    ];
    expect(declared.sort()).toEqual([...reference].sort());
  });

  it.each(palettes)("%s pins its own color-scheme", (theme) => {
    // Native controls follow this, not the custom properties.
    expect(
      declarationsAfter(`:root[data-theme="${theme}"]`).get("color-scheme"),
    ).toMatch(/^(light|dark)$/);
  });
});

describe("terminal palette", () => {
  // xterm paints a canvas, so it takes colours as JS config and cannot read a
  // custom property. That makes TerminalPanel.tsx a second copy of these four
  // tokens, and the two had already drifted apart — the panel green (#171b19),
  // the terminal blue (#171a20) — leaving a visible seam between them.
  const TERMINAL = readFileSync(
    resolve("src/features/workbench/TerminalPanel.tsx"),
    "utf8",
  );
  const rootDeclarations = declarationsAfter(":root {");

  it.each([
    ["--surface-terminal", "background"],
    ["--text-terminal", "foreground"],
  ])("gives %s the same value as xterm's %s", (variable, option) => {
    const token = rootDeclarations.get(variable);
    expect(token).toMatch(/^#[0-9a-f]{6}$/);
    expect(TERMINAL).toContain(`${option}: "${token}"`);
  });
});

describe("theme override selectors", () => {
  it("lets an explicit light choice beat a dark OS", () => {
    expect(CSS).toContain(':root:not([data-theme="light"])');
  });

  it("lets an explicit dark choice beat a light OS", () => {
    expect(CSS).toContain(':root[data-theme="dark"]');
  });
});

describe("appearance variables", () => {
  const rootDeclarations = declarationsAfter(":root {");

  it.each(["--conversation-width", "--font-size-base", "--font-ui-preferred"])(
    "declares a default for %s",
    (variable) => {
      expect(rootDeclarations.has(variable)).toBe(true);
    },
  );

  it("routes the root font through both variables", () => {
    expect(rootDeclarations.get("font-family")).toContain(
      "var(--font-ui-preferred)",
    );
    expect(rootDeclarations.get("font-size")).toBe("var(--font-size-base)");
  });
});
