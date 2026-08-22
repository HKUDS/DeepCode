/**
 * Capacity text round-trip for the model list editor: stored plain counts,
 * edited with K/M suffixes (1M = 1000K). Parsing returns null for blank,
 * NaN for text that does not parse — the caller leaves invalid text on
 * screen so a save-time rejection names a row that is still visible.
 */

/** Render a stored capacity for editing ("128K", "1M", exact count). */
export function capacityText(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  if (value >= 1_000_000 && value % 1_000_000 === 0)
    return `${value / 1_000_000}M`;
  if (value >= 1_000 && value % 1_000 === 0) return `${value / 1_000}K`;
  return String(value);
}

/** Parse a capacity with optional K/M suffix; null = blank, NaN = invalid. */
export function parseCapacity(text: string): number | null {
  const clean = text.trim();
  if (!clean) return null;
  const match = /^(\d+(?:\.\d+)?)\s*([kKmM]?)$/.exec(clean);
  if (!match) return Number.NaN;
  const base = Number(match[1]);
  const factor =
    match[2].toLowerCase() === "m"
      ? 1_000_000
      : match[2].toLowerCase() === "k"
        ? 1_000
        : 1;
  const value = Math.round(base * factor);
  return Number.isSafeInteger(value) && value > 0 ? value : Number.NaN;
}
