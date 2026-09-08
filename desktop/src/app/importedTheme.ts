import { parse, printParseErrorCode, type ParseError } from "jsonc-parser";

export const IMPORTED_THEME_TOKEN_NAMES = [
  "--surface-shell",
  "--surface-canvas",
  "--surface-sidebar",
  "--surface-sidebar-strong",
  "--surface-raised",
  "--surface-overlay",
  "--surface-hover",
  "--surface-selected",
  "--surface-user",
  "--surface-code",
  "--surface-inset",
  "--text-primary",
  "--text-secondary",
  "--text-tertiary",
  "--text-inverse",
  "--text-on-accent",
  "--border-subtle",
  "--border-strong",
  "--border-emphasis",
  "--signal",
  "--signal-strong",
  "--signal-soft",
  "--signal-faint",
  "--success",
  "--success-soft",
  "--attention",
  "--attention-soft",
  "--danger",
  "--danger-soft",
  "--shadow-soft",
  "--shadow-float",
  "--shadow-menu",
] as const;

export type ImportedThemeToken = (typeof IMPORTED_THEME_TOKEN_NAMES)[number];
export type ImportedThemeBase = "light" | "dark";
export type ImportedThemeTokens = Record<ImportedThemeToken, string>;

export interface ImportedTheme {
  name: string;
  base: ImportedThemeBase;
  tokens: ImportedThemeTokens;
}

export class ThemeImportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ThemeImportError";
  }
}

export const LIGHT_IMPORTED_THEME_BASE: ImportedThemeTokens = {
  "--surface-shell": "#e4e9e5",
  "--surface-canvas": "#f7f8f7",
  "--surface-sidebar": "#eef1ef",
  "--surface-sidebar-strong": "#e7ebe8",
  "--surface-raised": "#ffffff",
  "--surface-overlay": "rgb(255 255 255 / 88%)",
  "--surface-hover": "#e5e9e6",
  "--surface-selected": "#dde3e0",
  "--surface-user": "#edf0ef",
  "--surface-code": "#f0f2f1",
  "--surface-inset": "#e8ece9",
  "--text-primary": "#202321",
  "--text-secondary": "#474b48",
  "--text-tertiary": "#626964",
  "--text-inverse": "#f8faf9",
  "--text-on-accent": "#ffffff",
  "--border-subtle": "#dfe4e1",
  "--border-strong": "#cfd6d2",
  "--border-emphasis": "#aab4ae",
  "--signal": "#4d5bd5",
  "--signal-strong": "#4150bd",
  "--signal-soft": "rgb(77 91 213 / 13%)",
  "--signal-faint": "rgb(77 91 213 / 7%)",
  "--success": "#3a725e",
  "--success-soft": "#e7f1ed",
  "--attention": "#935b2f",
  "--attention-soft": "#f8eee6",
  "--danger": "#ab4850",
  "--danger-soft": "#f8e9eb",
  "--shadow-soft": "0 1px 2px rgb(27 34 30 / 4%)",
  "--shadow-float":
    "0 18px 48px rgb(27 34 30 / 10%), 0 3px 10px rgb(27 34 30 / 5%)",
  "--shadow-menu":
    "0 18px 42px rgb(27 34 30 / 14%), 0 3px 10px rgb(27 34 30 / 7%)",
};

export const DARK_IMPORTED_THEME_BASE: ImportedThemeTokens = {
  "--surface-shell": "#101211",
  "--surface-canvas": "#181b19",
  "--surface-sidebar": "#151816",
  "--surface-sidebar-strong": "#1b1f1c",
  "--surface-raised": "#222624",
  "--surface-overlay": "rgb(29 33 30 / 88%)",
  "--surface-hover": "#292e2b",
  "--surface-selected": "#303632",
  "--surface-user": "#282d2a",
  "--surface-code": "#151816",
  "--surface-inset": "#121513",
  "--text-primary": "#f1f4f2",
  "--text-secondary": "#b3bab5",
  "--text-tertiary": "#919892",
  "--text-inverse": "#151816",
  "--text-on-accent": "#ffffff",
  "--border-subtle": "#2b302d",
  "--border-strong": "#3a413d",
  "--border-emphasis": "#59625d",
  "--signal": "#929cff",
  "--signal-strong": "#aab2ff",
  "--signal-soft": "rgb(146 156 255 / 16%)",
  "--signal-faint": "rgb(146 156 255 / 8%)",
  "--success": "#6abb9c",
  "--success-soft": "rgb(63 125 103 / 17%)",
  "--attention": "#d79058",
  "--attention-soft": "rgb(178 100 43 / 14%)",
  "--danger": "#e4777e",
  "--danger-soft": "rgb(179 64 73 / 14%)",
  "--shadow-soft": "0 1px 2px rgb(0 0 0 / 16%)",
  "--shadow-float":
    "0 20px 52px rgb(0 0 0 / 32%), 0 3px 10px rgb(0 0 0 / 22%)",
  "--shadow-menu":
    "0 20px 46px rgb(0 0 0 / 42%), 0 3px 10px rgb(0 0 0 / 26%)",
};

const DIRECT_MAPPINGS: Partial<
  Record<ImportedThemeToken, readonly string[]>
> = {
  "--surface-shell": ["activityBar.background", "sideBar.background"],
  "--surface-canvas": ["editor.background"],
  "--surface-sidebar": ["sideBar.background"],
  "--surface-sidebar-strong": [
    "sideBarSectionHeader.background",
    "activityBar.background",
  ],
  "--surface-raised": ["editorWidget.background", "panel.background"],
  "--surface-overlay": ["editorWidget.background", "quickInput.background"],
  "--surface-hover": ["list.hoverBackground"],
  "--surface-selected": [
    "list.activeSelectionBackground",
    "list.inactiveSelectionBackground",
  ],
  "--surface-user": [
    "list.inactiveSelectionBackground",
    "editor.selectionBackground",
  ],
  "--surface-code": ["textCodeBlock.background", "editor.background"],
  "--surface-inset": ["input.background"],
  "--text-primary": ["foreground", "editor.foreground"],
  "--text-secondary": ["descriptionForeground"],
  "--text-tertiary": ["disabledForeground"],
  "--text-inverse": ["button.foreground", "activityBar.foreground"],
  "--text-on-accent": ["button.foreground"],
  "--border-subtle": ["widget.border", "panel.border"],
  "--border-strong": ["panel.border", "contrastBorder"],
  "--border-emphasis": ["contrastBorder", "focusBorder"],
  "--signal": ["focusBorder", "button.background"],
  "--signal-strong": ["button.hoverBackground", "focusBorder"],
  "--success": [
    "testing.iconPassed",
    "gitDecoration.addedResourceForeground",
  ],
  "--attention": ["editorWarning.foreground", "list.warningForeground"],
  "--danger": ["errorForeground", "editorError.foreground"],
};

const HEX_COLOR = /^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

export function parseVsCodeTheme(
  source: string,
  sourceLabel: string,
): ImportedTheme {
  const errors: ParseError[] = [];
  const decoded = parse(source, errors, {
    allowTrailingComma: true,
    disallowComments: false,
  }) as unknown;
  if (errors.length) {
    throw new ThemeImportError(
      `Invalid JSONC: ${printParseErrorCode(errors[0].error)}`,
    );
  }
  if (!isRecord(decoded)) {
    throw new ThemeImportError("The theme file must contain a JSON object.");
  }
  if ("include" in decoded) {
    throw new ThemeImportError(
      "Theme include chains are not supported yet; import one standalone color-theme file.",
    );
  }
  if (!isRecord(decoded.colors)) {
    throw new ThemeImportError("The theme file must define a colors object.");
  }

  const colors: Record<string, string> = {};
  for (const [name, value] of Object.entries(decoded.colors)) {
    if (typeof value !== "string" || !HEX_COLOR.test(value.trim())) {
      throw new ThemeImportError(
        `Invalid color for ${name}; VS Code theme colors must use hexadecimal notation.`,
      );
    }
    colors[name] = normalizeHex(value.trim());
  }

  const base = inferThemeBase(decoded, colors);
  const tokens: ImportedThemeTokens = {
    ...(base === "dark" ? DARK_IMPORTED_THEME_BASE : LIGHT_IMPORTED_THEME_BASE),
  };
  for (const token of IMPORTED_THEME_TOKEN_NAMES) {
    const mapped = firstColor(colors, DIRECT_MAPPINGS[token] ?? []);
    if (mapped) tokens[token] = mapped;
  }

  const signal = firstColor(colors, ["focusBorder", "button.background"]);
  if (signal) {
    tokens["--signal-soft"] = withAlpha(signal, 0.16);
    tokens["--signal-faint"] = withAlpha(signal, 0.08);
  }
  const success = firstColor(colors, [
    "testing.iconPassed",
    "gitDecoration.addedResourceForeground",
  ]);
  if (success) tokens["--success-soft"] = withAlpha(success, 0.16);
  const attention = firstColor(colors, [
    "editorWarning.foreground",
    "list.warningForeground",
  ]);
  if (attention) tokens["--attention-soft"] = withAlpha(attention, 0.15);
  const danger = firstColor(colors, [
    "errorForeground",
    "editorError.foreground",
  ]);
  if (danger) tokens["--danger-soft"] = withAlpha(danger, 0.15);
  const shadow = colors["widget.shadow"];
  if (shadow) {
    tokens["--shadow-soft"] = `0 1px 2px ${withAlpha(shadow, 0.16)}`;
    tokens["--shadow-float"] =
      `0 18px 48px ${withAlpha(shadow, 0.32)}, ` +
      `0 3px 10px ${withAlpha(shadow, 0.22)}`;
    tokens["--shadow-menu"] =
      `0 18px 42px ${withAlpha(shadow, 0.42)}, ` +
      `0 3px 10px ${withAlpha(shadow, 0.26)}`;
  }

  return {
    name: themeName(decoded.name, sourceLabel),
    base,
    tokens,
  };
}

export function sanitizeImportedTheme(value: unknown): ImportedTheme | null {
  if (!isRecord(value) || !isRecord(value.tokens)) return null;
  const base = value.base === "dark" ? "dark" : value.base === "light" ? "light" : null;
  if (!base) return null;
  const name = typeof value.name === "string" ? value.name.trim().slice(0, 120) : "";
  if (!name) return null;
  const tokens: ImportedThemeTokens = {
    ...(base === "dark" ? DARK_IMPORTED_THEME_BASE : LIGHT_IMPORTED_THEME_BASE),
  };
  for (const token of IMPORTED_THEME_TOKEN_NAMES) {
    const candidate = value.tokens[token];
    if (typeof candidate === "string" && safeStoredValue(candidate)) {
      tokens[token] = candidate.trim();
    }
  }
  return { name, base, tokens };
}

export function applyImportedTheme(
  theme: ImportedTheme | null,
  root: HTMLElement,
): void {
  for (const token of IMPORTED_THEME_TOKEN_NAMES) {
    root.style.removeProperty(token);
  }
  root.style.removeProperty("--imported-color-scheme");
  if (!theme) return;
  root.style.setProperty("--imported-color-scheme", theme.base);
  for (const token of IMPORTED_THEME_TOKEN_NAMES) {
    root.style.setProperty(token, theme.tokens[token]);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function firstColor(
  colors: Record<string, string>,
  names: readonly string[],
): string | null {
  for (const name of names) {
    if (colors[name]) return colors[name];
  }
  return null;
}

function normalizeHex(value: string): string {
  const raw = value.slice(1).toLowerCase();
  if (raw.length === 3 || raw.length === 4) {
    return `#${[...raw].map((part) => `${part}${part}`).join("")}`;
  }
  return `#${raw}`;
}

function withAlpha(value: string, opacity: number): string {
  const normalized = normalizeHex(value);
  const rgb = normalized.slice(0, 7);
  const sourceAlpha = normalized.length === 9 ? parseInt(normalized.slice(7), 16) / 255 : 1;
  const alpha = Math.round(255 * sourceAlpha * opacity)
    .toString(16)
    .padStart(2, "0");
  return `${rgb}${alpha}`;
}

function inferThemeBase(
  decoded: Record<string, unknown>,
  colors: Record<string, string>,
): ImportedThemeBase {
  const declared = String(decoded.type ?? decoded.uiTheme ?? "").toLowerCase();
  if (declared.includes("dark")) return "dark";
  if (declared.includes("light")) return "light";
  const background = colors["editor.background"];
  if (!background) return "light";
  const rgb = background
    .slice(1, 7)
    .match(/.{2}/g)
    ?.map((part) => parseInt(part, 16) / 255);
  if (!rgb || rgb.length !== 3) return "light";
  const luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  return luminance < 0.5 ? "dark" : "light";
}

function themeName(value: unknown, sourceLabel: string): string {
  if (typeof value === "string" && value.trim()) return value.trim().slice(0, 120);
  const fallback = sourceLabel
    .replace(/\.(?:jsonc?|code-workspace)$/i, "")
    .replace(/[-_]?color[-_]?theme$/i, "")
    .trim();
  return (fallback || "Imported theme").slice(0, 120);
}

function safeStoredValue(value: string): boolean {
  const clean = value.trim().toLowerCase();
  return (
    clean.length > 0 &&
    clean.length <= 240 &&
    !/[;{}]/.test(clean) &&
    !clean.includes("url(") &&
    !clean.includes("var(")
  );
}
