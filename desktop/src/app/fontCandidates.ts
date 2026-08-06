/**
 * Font suggestions filtered to what the machine actually has.
 *
 * A plain dropdown of font names would be a hardcoded guess: the list that is
 * right on a Windows box with Microsoft YaHei is wrong on a Mac with PingFang,
 * and offering a family the system lacks produces a setting that silently does
 * nothing. `document.fonts.check()` answers the question directly, so the
 * candidates below are only ever *suggestions* — the UI shows the survivors
 * and still accepts free text for anything not listed.
 */

export interface FontCandidate {
  family: string;
  /** Grouping for the UI; also documents why each family is here. */
  group: "Interface" | "Monospace" | "CJK";
}

/**
 * Families worth offering when present. Deliberately broad and cross-platform
 * — an entry that is missing costs nothing because it is filtered out.
 */
export const FONT_CANDIDATES: readonly FontCandidate[] = [
  { family: "Inter", group: "Interface" },
  { family: "Segoe UI Variable", group: "Interface" },
  { family: "Segoe UI", group: "Interface" },
  { family: "SF Pro Text", group: "Interface" },
  { family: "Helvetica Neue", group: "Interface" },
  { family: "Roboto", group: "Interface" },
  { family: "JetBrains Mono", group: "Monospace" },
  { family: "Cascadia Code", group: "Monospace" },
  { family: "Fira Code", group: "Monospace" },
  { family: "SFMono-Regular", group: "Monospace" },
  { family: "Consolas", group: "Monospace" },
  { family: "Microsoft YaHei", group: "CJK" },
  { family: "PingFang SC", group: "CJK" },
  { family: "Noto Sans SC", group: "CJK" },
  { family: "Source Han Sans SC", group: "CJK" },
  { family: "Sarasa Mono SC", group: "CJK" },
  { family: "Hiragino Sans GB", group: "CJK" },
];

/**
 * Whether `family` resolves on this machine.
 *
 * `document.fonts.check` needs a full font shorthand and throws on a malformed
 * one, so the family is quoted and the call is guarded. An environment without
 * the Font Loading API (jsdom, an old WebView) reports nothing rather than
 * pretending every family exists.
 */
export function isFontAvailable(family: string): boolean {
  if (typeof document === "undefined" || !document.fonts?.check) return false;
  try {
    return document.fonts.check(`12px "${family.replaceAll('"', "")}"`);
  } catch {
    return false;
  }
}

/** The candidates present on this machine, in declaration order. */
export function availableFontCandidates(): FontCandidate[] {
  return FONT_CANDIDATES.filter((candidate) => isFontAvailable(candidate.family));
}

/** Append `family` to a comma-separated list, ignoring duplicates. */
export function appendFamily(current: string, family: string): string {
  const families = current
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
  if (families.some((entry) => entry.toLowerCase() === family.toLowerCase())) {
    return current;
  }
  return [...families, family].join(", ");
}
