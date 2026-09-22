/**
 * Font suggestions filtered to what the machine actually has.
 *
 * A plain dropdown of font names would be a hardcoded guess: the list that is
 * right on a Windows box with Microsoft YaHei is wrong on a Mac with PingFang,
 * and offering a family the system lacks produces a setting that silently does
 * nothing. The candidates below are therefore only ever *suggestions* — the UI
 * shows the survivors and still accepts free text for anything not listed.
 *
 * Presence comes from a `local()` lookup, not from `document.fonts.check()`.
 * That call is the obvious API and it does not answer this question: it reports
 * whether the text *can* be rendered, and a family the machine lacks still
 * renders through the fallback. Measured on Edge/WebView2 153.0.4234 — the
 * engine Tauri uses on Windows — `check()` answered true for all 39 families in
 * a sweep that included `__Absent Font 12345__`, so every candidate survived
 * the filter and the picker offered fonts the machine did not have. `local()`
 * goes through font matching instead: it rejected every family with no font on
 * the machine (Roboto, Helvetica, PingFang SC, …) while resolving the rest, and
 * agreed with the Windows font registry on every name it was asked about.
 * `queryLocalFonts()` would enumerate the system list directly, but it needs a
 * user gesture and a permission grant, so it cannot back a picker that is
 * populated when the settings page opens.
 *
 * The probe is asynchronous, and an engine that cannot probe reports nothing
 * rather than pretending every family exists.
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
  // Windows 11 ships its UI face as one variable font, and the system exposes
  // its optical sizes as separate families; the bare "Segoe UI Variable" is not
  // one of them. Measured on this engine: a local() lookup of the bare name
  // fails, while "… Text" — the size used at body text — resolves.
  { family: "Segoe UI Variable Text", group: "Interface" },
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

/** Name for the throwaway face a probe loads. It is never registered. */
const PROBE_FACE_NAME = "deepcode-font-probe";

/** Quote a family for a `local()` source, dropping characters that would end it. */
function quote(family: string): string {
  return `"${family.replaceAll('"', "").replaceAll("\\", "")}"`;
}

/**
 * Whether the engine resolves `family` by name.
 *
 * `local()` is the lookup that fails for a family the machine lacks, so a
 * failed load is the negative answer. An engine without `FontFace` — or one
 * that refuses local lookups — reports nothing rather than claiming every
 * family exists. The face is never added to `document.fonts`, so probing
 * leaves no trace.
 */
export async function isFontAvailable(family: string): Promise<boolean> {
  if (typeof FontFace === "undefined") return false;
  try {
    await new FontFace(PROBE_FACE_NAME, `local(${quote(family)})`).load();
    return true;
  } catch {
    return false;
  }
}

/** The candidates present on this machine, in declaration order. */
export async function availableFontCandidates(): Promise<FontCandidate[]> {
  const verdicts = await Promise.all(
    FONT_CANDIDATES.map(async (candidate) => ({
      candidate,
      present: await isFontAvailable(candidate.family),
    })),
  );
  return verdicts
    .filter((verdict) => verdict.present)
    .map((verdict) => verdict.candidate);
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
