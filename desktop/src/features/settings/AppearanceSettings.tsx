import { Monitor, Moon, Sun } from "lucide-react";
import { useMemo, useId } from "react";

import {
  APPEARANCE_DEFAULTS,
  APPEARANCE_SETTINGS,
  THEME_PREFERENCES,
  type AppearanceState,
  type ThemePreference,
} from "../../app/appearance";
import {
  appendFamily,
  availableFontCandidates,
} from "../../app/fontCandidates";
import { useAppearance } from "../../app/useAppearance";
import { useTranslation } from "react-i18next";
import styles from "../management/ManagementWorkspace.module.css";
import modeStyles from "./AppearanceSettings.module.css";

// The dsh tri-state: the three modes everyone reaches for, as cards. The
// full palette list (Paper, Midnight, Claude, …) stays in the advanced
// select below; picking a palette there simply means none of the three
// cards is pressed.
const MODE_CARDS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const satisfies readonly {
  value: ThemePreference;
  label: string;
  icon: typeof Sun;
}[];

// Record, not Partial — TypeScript fails the build if a palette is added to
// THEME_PREFERENCES without a name to show for it.
const THEME_LABELS: Record<ThemePreference, string> = {
  system: "Match system",
  light: "Light",
  dark: "Dark",
  paper: "Paper — warm, low blue",
  midnight: "Midnight — deep, cool",
  claude: "Claude — ivory & terracotta",
  "claude-dark": "Claude Dark — slate & terracotta",
  contrast: "High contrast — AAA",
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
  const { t } = useTranslation();
  const fieldId = useId();
  // Probed once per mount: the set of installed fonts does not change while
  // the settings page is open.
  const installed = useMemo(() => availableFontCandidates(), []);
  const isDefault = APPEARANCE_SETTINGS.every(
    (setting) => appearance[setting.key] === APPEARANCE_DEFAULTS[setting.key],
  );

  return (
    <section className={styles.formCard}>
      <header>
        <div>
          <p className={styles.eyebrow}>
            {t("settings.appearance.eyebrow", "Display")}
          </p>
          <h2>{t("settings.appearance.title", "Appearance")}</h2>
        </div>
      </header>

      <div
        className={modeStyles.modeRow}
        role="group"
        aria-label="Appearance mode"
      >
        {MODE_CARDS.map((mode) => {
          const Icon = mode.icon;
          return (
            <button
              key={mode.value}
              type="button"
              className={modeStyles.modeCard}
              aria-pressed={appearance.theme === mode.value}
              onClick={() => set("theme", mode.value)}
            >
              <Icon size={18} />
              {mode.label}
            </button>
          );
        })}
      </div>

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
              {installed.length > 0 ? (
                <select
                  aria-label="Add an installed font"
                  value=""
                  onChange={(event) => {
                    if (!event.target.value) return;
                    set(
                      "fontFamily",
                      appendFamily(appearance.fontFamily, event.target.value),
                    );
                  }}
                >
                  <option value="">Add an installed font…</option>
                  {["Interface", "Monospace", "CJK"].map((group) => {
                    const members = installed.filter(
                      (candidate) => candidate.group === group,
                    );
                    if (members.length === 0) return null;
                    return (
                      <optgroup key={group} label={group}>
                        {members.map((candidate) => (
                          <option
                            key={candidate.family}
                            value={candidate.family}
                          >
                            {candidate.family}
                          </option>
                        ))}
                      </optgroup>
                    );
                  })}
                </select>
              ) : null}
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
