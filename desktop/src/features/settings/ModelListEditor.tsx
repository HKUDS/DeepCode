/**
 * Model list editor — dsh's row grammar: one bordered row per declared
 * model with Model ID and Display name up front and capacities behind the
 * row's own disclosure. Capacity fields accept K/M suffixes (1M = 1000K)
 * and store plain counts; text that does not parse stays on screen so the
 * save-time rejection names a row that is still visible. Rows are the
 * config file's own entries; supported declarations are edited field-wise
 * while an entry with only an id stays a plain id when saved.
 *
 * Effort declarations (`reasoningEfforts`) are config-file-only, exactly
 * as in dsh: a per-model ladder is a capability statement, not a form
 * field, and the editor's footer says where it lives.
 *
 * Every field is CONTROLLED by the entry it renders. An uncontrolled
 * capacity input kept the DOM's own text when a row was removed, so the
 * surviving row displayed — and then saved — the deleted model's
 * capacities. Deriving each input from props makes a row's display follow
 * its data by construction.
 */

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { useTranslation } from "react-i18next";
import { CompatEditor } from "./ProtocolSettings";
import type {
  ManualModelEntry,
  ProviderProtocol,
} from "../../generated/app-server";
import { capacityText, parseCapacity } from "./modelCapacity";
import styles from "./ConnectionSettings.module.css";

interface ModelListEditorProps {
  protocol?: ProviderProtocol;
  entries: ManualModelEntry[];
  onChange(entries: ManualModelEntry[]): void;
}

export function ModelListEditor({
  entries,
  onChange,
  protocol = "auto",
}: ModelListEditorProps) {
  const { t } = useTranslation();
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
              onClick={() => onChange(entries.filter((_, at) => at !== index))}
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
          <details>
            <summary>
              {t("provider.capabilities", "Model capabilities")}
            </summary>
            <label>
              {t("provider.inputModalities", "Input modalities")}
              <select
                value={entry.inputModalities?.join(",") ?? ""}
                onChange={(event) =>
                  update(index, {
                    inputModalities: event.target.value
                      ? (event.target.value.split(",") as [
                          "text" | "image",
                          ...("text" | "image")[],
                        ])
                      : null,
                  })
                }
              >
                <option value="">{t("provider.inherit", "Inherit")}</option>
                <option value="text">
                  {t("provider.textOnly", "Text only")}
                </option>
                <option value="text,image">
                  {t("provider.textImage", "Text and image")}
                </option>
                {entry.inputModalities &&
                !["text", "text,image"].includes(
                  entry.inputModalities.join(","),
                ) ? (
                  <option value={entry.inputModalities.join(",")}>
                    {entry.inputModalities.join(", ")}
                  </option>
                ) : null}
              </select>
            </label>
            <label>
              {t("provider.toolCalling", "Tool calling")}
              <select
                value={
                  entry.toolCalling == null ? "" : String(entry.toolCalling)
                }
                onChange={(event) =>
                  update(index, {
                    toolCalling:
                      event.target.value === ""
                        ? null
                        : event.target.value === "true",
                  })
                }
              >
                <option value="">{t("provider.inherit", "Inherit")}</option>
                <option value="true">{t("provider.yes", "Yes")}</option>
                <option value="false">{t("provider.no", "No")}</option>
              </select>
            </label>
            <CompatEditor
              protocol={protocol}
              value={entry.compat ?? {}}
              onChange={(compat) => update(index, { compat })}
            />
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
  // Controlled by the stored value, with a local draft only while the text
  // does not parse — so a value that WAS accepted can never linger on
  // screen after the row it belonged to is gone.
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <label>
      {label}
      <input
        value={draft ?? capacityText(value)}
        placeholder="Inherit · e.g. 128K or 1M"
        onChange={(event) => {
          const text = event.target.value;
          setDraft(text);
          const parsed = parseCapacity(text);
          // Unparseable text stays on screen; the stored value only moves
          // when the text is a real capacity.
          if (!Number.isNaN(parsed)) onChange(parsed);
        }}
        onBlur={() => setDraft(null)}
      />
    </label>
  );
}
