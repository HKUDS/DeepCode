import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Vitest runs from the Vite root (desktop/); jsdom leaves import.meta.url as
// a non-file URL, so resolve against the project root instead.
const CSS = readFileSync(resolve("src/styles/tokens.css"), "utf8");

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
