/**
 * Appearance preferences: one declarative table, one apply path.
 *
 * Conversation width (#151), theme (#152) and typography (#155) are the same
 * shape of problem — a value the user picks, persisted locally, surfaced to
 * CSS. Writing three hooks would mean three copies of read/validate/persist
 * and three chances for them to drift, so each setting is a row in
 * `APPEARANCE_SETTINGS` and the machinery below is shared.
 *
 * Values reach the UI as custom properties on `:root`. Nothing imports this
 * module to read a preference mid-render: stylesheets consume the variables,
 * which keeps components free of appearance conditionals.
 */

const STORAGE_KEY = "deepcode.desktop.appearance.v1";

/**
 * Follow the OS, or pin one palette regardless of it.
 *
 * Each name past "system" is also a `:root[data-theme="…"]` block in
 * tokens.css; adding one is those two edits and nothing else, because the
 * value only ever reaches CSS. Dropping one is safe too — `sanitize` returns
 * a stored name that is no longer listed to the default.
 */
export type ThemePreference =
  | "system"
  | "light"
  | "dark"
  | "paper"
  | "midnight"
  | "claude"
  | "claude-dark"
  | "contrast";

export const THEME_PREFERENCES: readonly ThemePreference[] = [
  "system",
  "light",
  "dark",
  "paper",
  "midnight",
  "claude",
  "claude-dark",
  "contrast",
];

export interface AppearanceState {
  /** Percentage of the available width the conversation column occupies. */
  conversationWidth: number;
  theme: ThemePreference;
  /** Base UI font size in px; everything else is relative to it. */
  fontSize: number;
  /**
   * Extra families tried before the built-in stack. Free text rather than a
   * curated dropdown: the fonts a user actually has are theirs to know, and a
   * fixed list would be both wrong and stale. Empty means "use the default".
   */
  fontFamily: string;
}

export const APPEARANCE_DEFAULTS: AppearanceState = {
  conversationWidth: 100,
  theme: "system",
  fontSize: 14,
  fontFamily: "",
};

/**
 * How one preference is validated and handed to CSS.
 *
 * `cssVariable` is `null` for settings applied some other way — the theme is
 * a `data-theme` attribute, because a stylesheet cannot switch on a variable.
 */
interface AppearanceSetting<K extends keyof AppearanceState> {
  key: K;
  label: string;
  description: string;
  cssVariable: string | null;
  /** Coerce anything (stale storage, a bad edit) into a usable value. */
  sanitize(value: unknown): AppearanceState[K];
  /** Render the stored value as the CSS custom property's value. */
  toCss?(value: AppearanceState[K]): string;
  /** Present for numeric settings so the UI can build a slider. */
  range?: { min: number; max: number; step: number; unit: string };
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  const parsed = typeof value === "string" ? Number(value) : value;
  if (typeof parsed !== "number" || !Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

const CONVERSATION_WIDTH: AppearanceSetting<"conversationWidth"> = {
  key: "conversationWidth",
  label: "Conversation width",
  description:
    "How much of the window the conversation column fills. The default keeps " +
    "lines short for readability; widen it to use more of a large display.",
  cssVariable: "--conversation-width",
  range: { min: 40, max: 100, step: 5, unit: "%" },
  sanitize: (value) =>
    clampNumber(value, 40, 100, APPEARANCE_DEFAULTS.conversationWidth),
  // 100% restores the built-in cap rather than stretching edge to edge, so
  // the default stays exactly what it was before this setting existed.
  toCss: (value) => (value >= 100 ? "min(820px, 100%)" : `${value}%`),
};

const THEME: AppearanceSetting<"theme"> = {
  key: "theme",
  label: "Theme",
  description:
    "'System' follows the OS setting; every other choice overrides it. Paper " +
    "is warmer and lower in blue, Midnight deeper and cooler, Claude an ivory " +
    "and terracotta pair. High contrast clears WCAG AAA throughout.",
  cssVariable: null,
  sanitize: (value) =>
    THEME_PREFERENCES.includes(value as ThemePreference)
      ? (value as ThemePreference)
      : APPEARANCE_DEFAULTS.theme,
};

const FONT_SIZE: AppearanceSetting<"fontSize"> = {
  key: "fontSize",
  label: "Font size",
  description: "Base interface text size. Other sizes scale with it.",
  cssVariable: "--font-size-base",
  range: { min: 11, max: 22, step: 1, unit: "px" },
  sanitize: (value) => clampNumber(value, 11, 22, APPEARANCE_DEFAULTS.fontSize),
  toCss: (value) => `${value}px`,
};

const FONT_FAMILY: AppearanceSetting<"fontFamily"> = {
  key: "fontFamily",
  label: "Preferred fonts",
  description:
    "Comma-separated families tried before the built-in stack — useful for " +
    "picking a CJK face so mixed text renders consistently. Anything the " +
    "system lacks is skipped, so listing several is safe.",
  cssVariable: "--font-ui-preferred",
  sanitize: (value) => (typeof value === "string" ? value.trim().slice(0, 200) : ""),
  // Trailing comma: this list is a prefix of the built-in stack, never the
  // whole of it, so an unavailable font can always fall through.
  toCss: (value) => (value ? `${value},` : ""),
};

export const APPEARANCE_SETTINGS = [
  CONVERSATION_WIDTH,
  THEME,
  FONT_SIZE,
  FONT_FAMILY,
] as const;

/** Coerce a parsed blob of unknown provenance into a complete state. */
export function sanitizeAppearance(value: unknown): AppearanceState {
  const raw = typeof value === "object" && value !== null ? value : {};
  const source = raw as Record<string, unknown>;
  return APPEARANCE_SETTINGS.reduce(
    (state, setting) => ({
      ...state,
      [setting.key]: setting.sanitize(source[setting.key]),
    }),
    { ...APPEARANCE_DEFAULTS },
  );
}

export function readAppearance(): AppearanceState {
  try {
    return sanitizeAppearance(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}"));
  } catch {
    // Appearance is a convenience; unreadable storage must not block startup.
    return { ...APPEARANCE_DEFAULTS };
  }
}

export function writeAppearance(state: AppearanceState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Quota or a private-mode window: the session still honours the choice.
  }
}

/**
 * Push the state onto `root`, clearing anything left at its default so the
 * stylesheet's own value shows through instead of a duplicate copy of it.
 */
export function applyAppearance(state: AppearanceState, root: HTMLElement): void {
  for (const setting of APPEARANCE_SETTINGS) {
    if (!setting.cssVariable) continue;
    const value = state[setting.key] as never;
    const rendered = setting.toCss ? setting.toCss(value) : String(value);
    if (rendered === "" || value === APPEARANCE_DEFAULTS[setting.key]) {
      root.style.removeProperty(setting.cssVariable);
    } else {
      root.style.setProperty(setting.cssVariable, rendered);
    }
  }

  if (state.theme === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", state.theme);
  }
}
