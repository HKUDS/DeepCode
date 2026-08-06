import { useId } from "react";

import {
  APPEARANCE_DEFAULTS,
  APPEARANCE_SETTINGS,
  THEME_PREFERENCES,
  type AppearanceState,
  type ThemePreference,
} from "../../app/appearance";
import { useAppearance } from "../../app/useAppearance";
import styles from "../management/ManagementWorkspace.module.css";

const THEME_LABELS: Record<ThemePreference, string> = {
  system: "Match system",
  light: "Light",
  dark: "Dark",
};

/**
 * Appearance controls, rendered from `APPEARANCE_SETTINGS`.
 *
 * The table owns each preference's label, range and validation, so adding one
 * is a row there rather than another block of near-identical JSX here.
 * Changes apply immediately and persist locally — these are per-machine
 * display choices, not project configuration, so there is nothing to save.
 */
export function AppearanceSettings() {
  const { appearance, set, reset } = useAppearance();
  const fieldId = useId();
  const isDefault = APPEARANCE_SETTINGS.every(
    (setting) => appearance[setting.key] === APPEARANCE_DEFAULTS[setting.key],
  );

  return (
    <section className={styles.formCard}>
      <header>
        <div>
          <p className={styles.eyebrow}>Display</p>
          <h2>Appearance</h2>
        </div>
      </header>

      <div className={styles.formGrid}>
        {APPEARANCE_SETTINGS.map((setting) => {
          const id = `${fieldId}-${setting.key}`;
          const value = appearance[setting.key];

          if (setting.key === "theme") {
            return (
              <label key={setting.key} htmlFor={id}>
                {setting.label}
                <select
                  id={id}
                  value={value as ThemePreference}
                  onChange={(event) =>
                    set("theme", event.target.value as ThemePreference)
                  }
                >
                  {THEME_PREFERENCES.map((preference) => (
                    <option key={preference} value={preference}>
                      {THEME_LABELS[preference]}
                    </option>
                  ))}
                </select>
              </label>
            );
          }

          if (setting.range) {
            const { min, max, step, unit } = setting.range;
            return (
              <label key={setting.key} htmlFor={id}>
                {setting.label}
                <span className={styles.note}>
                  {value}
                  {unit}
                </span>
                <input
                  id={id}
                  type="range"
                  min={min}
                  max={max}
                  step={step}
                  value={value as number}
                  onChange={(event) =>
                    set(
                      setting.key as "conversationWidth" | "fontSize",
                      Number(event.target.value),
                    )
                  }
                />
              </label>
            );
          }

          return (
            <label key={setting.key} htmlFor={id}>
              {setting.label}
              <input
                id={id}
                type="text"
                value={value as string}
                placeholder="e.g. Sarasa Mono SC, Inter"
                onChange={(event) =>
                  set(setting.key as "fontFamily", event.target.value)
                }
              />
            </label>
          );
        })}
      </div>

      <p className={styles.note}>
        {
          APPEARANCE_SETTINGS.find((setting) => setting.key === "fontFamily")
            ?.description
        }
      </p>

      <footer className={styles.formActions}>
        <span>
          These are per-machine display settings. They apply immediately and
          are not part of project configuration.
        </span>
        <button
          className={styles.primaryButton}
          type="button"
          disabled={isDefault}
          onClick={reset}
        >
          Reset to defaults
        </button>
      </footer>
    </section>
  );
}

export type { AppearanceState };
