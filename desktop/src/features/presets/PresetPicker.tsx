import { UserRound } from "lucide-react";

import type { AgentPresetEntry } from "../../generated/app-server";
import styles from "./PresetPicker.module.css";

interface PresetPickerProps {
  entries: AgentPresetEntry[];
  current: string | null;
  /** True once the thread has any Turn — the preset is then fixed. */
  locked: boolean;
  busy: boolean;
  error: string | null;
  onSelect(presetId: string | null): void;
}

const LOCKED_HINT =
  "This session already started; its agent preset is fixed. Start a new session to use a different one.";
const PICK_HINT = "Agent preset — persona and tool face for this session";

/** Agent-preset control for the composer toolbar.
 *
 * One control, two honest states (the dsh pattern): a picker while the
 * session is blank, a read-only label of what the session runs once the
 * conversation has started. Broken presets never appear as options —
 * offering one would only defer its failure to session start.
 */
export function PresetPicker({
  entries,
  current,
  locked,
  busy,
  error,
  onSelect,
}: PresetPickerProps) {
  const selectable = entries.filter((entry) => entry.broken === null);
  // Nothing to offer and nothing to report: the control vanishes entirely.
  if (selectable.length === 0 && current === null) return null;
  const title = error ?? (locked ? LOCKED_HINT : PICK_HINT);
  return (
    <label
      className={error ? `${styles.picker} ${styles.pickerError}` : styles.picker}
      title={title}
    >
      <UserRound size={12} aria-hidden="true" />
      <select
        aria-label="Agent preset"
        value={current ?? ""}
        disabled={locked || busy}
        onChange={(event) => onSelect(event.target.value || null)}
      >
        <option value="">Default</option>
        {selectable.map((entry) => (
          <option key={entry.id} value={entry.id} title={entry.description}>
            {entry.name}
          </option>
        ))}
        {current !== null &&
        !selectable.some((entry) => entry.id === current) ? (
          // A locked session may run a preset the roster no longer offers;
          // still name what it runs instead of showing a blank control.
          <option value={current}>{current}</option>
        ) : null}
      </select>
    </label>
  );
}
