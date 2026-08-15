/**
 * Default agent preset for NEW Sessions (dsh's `agent-presets.default`).
 * Writes `agents.defaults.defaultPreset`; the application layer resolves it
 * at Session creation through the same by-value snapshot an explicit pick
 * uses, so running Sessions keep the preset they began with.
 */

import { useEffect, useState } from "react";

import type { AgentPresetEntry, SettingsSnapshot } from "../../../generated/app-server";
import type { SettingsSectionProps } from "../settingsSections";
import { useTranslation } from "react-i18next";
import styles from "../../management/ManagementWorkspace.module.css";

function configuredDefaultPreset(settings: SettingsSnapshot | null): string {
  const agents = settings?.agents;
  if (typeof agents !== "object" || agents === null) return "";
  const defaults = (agents as Record<string, unknown>).defaults;
  if (typeof defaults !== "object" || defaults === null) return "";
  const value = (defaults as Record<string, unknown>).defaultPreset;
  return typeof value === "string" ? value : "";
}

export function DefaultPresetCard({
  runtime,
  project,
  settings,
  busy,
  scope,
  onUpdate,
}: SettingsSectionProps) {
  const { t } = useTranslation();
  const projectId = project?.id ?? null;
  const [roster, setRoster] = useState<{
    projectId: string;
    entries: AgentPresetEntry[];
  } | null>(null);
  const [draft, setDraft] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!projectId) return;
    let stale = false;
    runtime
      .request("preset/list", { projectId })
      .then((result) => {
        if (!stale) setRoster({ projectId, entries: result.presets });
      })
      .catch(() => {
        if (!stale) setRoster({ projectId, entries: [] });
      });
    return () => {
      stale = true;
    };
  }, [projectId, runtime]);

  const entries = roster?.projectId === projectId ? roster.entries : [];
  const configured = configuredDefaultPreset(settings);
  const selection = draft === undefined ? configured : draft;
  // The configured id may name a preset this project cannot see; keep it
  // selectable so the row shows the truth instead of silently blanking.
  const knownIds = new Set(entries.map((entry) => entry.id));

  const save = async () => {
    await onUpdate(
      { agents: { defaults: { defaultPreset: selection || null } } },
      scope,
    );
    setDraft(undefined);
  };

  return (
    <section className={styles.formCard}>
      <header>
        <div>
          <p className={styles.eyebrow}>
            {t("settings.preset.eyebrow", "Session defaults")}
          </p>
          <h2>{t("settings.preset.title", "Agent preset")}</h2>
        </div>
      </header>
      <div className={styles.formGrid}>
        <label>
          Default for new Sessions
          <select
            value={selection}
            onChange={(event) => setDraft(event.target.value)}
          >
            <option value="">None · default composition</option>
            {selection && !knownIds.has(selection) ? (
              <option value={selection}>{selection} · not found here</option>
            ) : null}
            {entries.map((entry) => (
              <option key={entry.id} value={entry.id} disabled={!!entry.broken}>
                {entry.name} [{entry.trust}]
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className={styles.note}>
        Applies to Sessions you start from now on. Running Sessions keep the
        preset they began with; a blank Session can still switch or clear its
        preset from the composer.
      </p>
      <footer className={styles.formActions}>
        <span>
          An unresolvable name is ignored at Session creation rather than
          blocking it.
        </span>
        <button
          className={styles.primaryButton}
          type="button"
          disabled={busy || draft === undefined || draft === configured}
          onClick={() => void save()}
        >
          Save preset default
        </button>
      </footer>
    </section>
  );
}
