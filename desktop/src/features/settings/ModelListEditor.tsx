/**
 * Model list editor — dsh's row grammar: one bordered row per declared
 * model with Model ID and Display name up front and capacities behind the
 * row's own disclosure. Capacity fields accept K/M suffixes (1M = 1000K)
 * and store plain counts; text that does not parse stays on screen so the
 * save-time rejection names a row that is still visible. Rows are the
 * config file's own entries — unknown future fields survive because plain
 * ids stay plain and objects are edited field-wise, never rebuilt.
 *
 * Effort declarations (`reasoningEfforts`) are config-file-only, exactly
 * as in dsh: a per-model ladder is a capability statement, not a form
 * field, and the editor's footer says where it lives.
 */

import { Plus, Trash2 } from "lucide-react";

import type { ManualModelEntry } from "../../generated/app-server";
import { capacityText, parseCapacity } from "./modelCapacity";
import styles from "./ConnectionSettings.module.css";

interface ModelListEditorProps {
  entries: ManualModelEntry[];
  onChange(entries: ManualModelEntry[]): void;
}

export function ModelListEditor({ entries, onChange }: ModelListEditorProps) {
  const update = (index: number, patch: Partial<ManualModelEntry>) => {
    onChange(
      entries.map((entry, at) =>
        at === index ? { ...entry, ...patch } : entry,
      ),
    );
  };

  return (
    <div className={styles.modelRows}>
      {entries.map((entry, index) => (
        <div className={styles.modelRow} key={index}>
          <div className={styles.modelRowMain}>
            <label>
              Model ID
              <input
                value={entry.id}
                onChange={(event) => update(index, { id: event.target.value })}
                placeholder="provider/model-id"
              />
            </label>
            <label>
              Display name
              <input
                value={entry.label ?? ""}
                onChange={(event) =>
                  update(index, { label: event.target.value || null })
                }
                placeholder="Optional"
              />
            </label>
            <button
              type="button"
              className={styles.modelRowRemove}
              aria-label={`Remove model ${entry.id || index + 1}`}
              onClick={() =>
                onChange(entries.filter((_, at) => at !== index))
              }
            >
              <Trash2 size={13} />
            </button>
          </div>
          <details>
            <summary>Capacities</summary>
            <div className={styles.modelRowCapacities}>
              <CapacityField
                label="Context window"
                value={entry.contextWindow}
                onChange={(contextWindow) => update(index, { contextWindow })}
              />
              <CapacityField
                label="Max output tokens"
                value={entry.maxOutputTokens}
                onChange={(maxOutputTokens) =>
                  update(index, { maxOutputTokens })
                }
              />
            </div>
          </details>
        </div>
      ))}
      <button
        type="button"
        className={styles.modelRowAdd}
        onClick={() => onChange([...entries, { id: "" }])}
      >
        <Plus size={13} /> Add model
      </button>
    </div>
  );
}

function CapacityField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number | null | undefined;
  onChange(value: number | null): void;
}) {
  return (
    <label>
      {label}
      <input
        defaultValue={capacityText(value)}
        placeholder="Inherit · e.g. 128K or 1M"
        onBlur={(event) => {
          const parsed = parseCapacity(event.target.value);
          if (Number.isNaN(parsed)) return; // stays visible; save rejects
          onChange(parsed);
          event.target.value = capacityText(parsed);
        }}
      />
    </label>
  );
}
