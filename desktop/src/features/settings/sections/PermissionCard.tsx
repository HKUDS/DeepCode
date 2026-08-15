/**
 * Default Session access — extracted from the old SettingsPage with the
 * full-access risk acknowledgement chain intact (confirm here, dispatcher
 * check server-side, frozen per-turn snapshot beyond that).
 */

import { useState } from "react";

import type {
  ConfigScope,
  ExecutionAccessPreset,
  JsonObject,
  SettingsSnapshot,
} from "../../../generated/app-server";
import {
  ACCESS_PRESET_OPTIONS,
  settingsDefaultAccessLabel,
} from "../../../app/accessPreset";
import { confirmAction } from "../../../platform/confirmAction";
import { useTranslation } from "react-i18next";
import styles from "../../management/ManagementWorkspace.module.css";

interface PermissionCardProps {
  settings: SettingsSnapshot | null;
  busy: boolean;
  scope: ConfigScope;
  onUpdate(
    patch: JsonObject,
    scope: ConfigScope,
    riskAcknowledged?: boolean,
  ): Promise<void>;
}

export function PermissionCard({
  settings,
  busy,
  scope,
  onUpdate,
}: PermissionCardProps) {
  const { t } = useTranslation();
  const [accessPresetDraft, setAccessPresetDraft] = useState<
    ExecutionAccessPreset | null | undefined
  >(undefined);
  const configuredAccessPreset =
    scope === "project"
      ? (settings?.projectAccessPreset ?? null)
      : (settings?.userAccessPreset ?? null);
  const accessPreset =
    accessPresetDraft === undefined ? configuredAccessPreset : accessPresetDraft;

  const saveSecurity = async () => {
    if (
      accessPreset === "full_access" &&
      !(await confirmAction(
        "Full access becomes the default for Sessions without an override. " +
          "Tools may run without approval and outside the workspace sandbox.",
        {
          title: "Use Full access by default?",
          kind: "warning",
          confirmLabel: "Save Full access default",
          cancelLabel: "Cancel",
        },
      ))
    ) {
      return;
    }
    await onUpdate(
      { security: { accessPreset } },
      scope,
      accessPreset === "full_access",
    );
    setAccessPresetDraft(undefined);
  };

  return (
    <section className={styles.formCard}>
      <header>
        <div>
          <p className={styles.eyebrow}>
            {t("settings.permissions.eyebrow", "Safety policy")}
          </p>
          <h2>{t("settings.permissions.title", "Permissions")}</h2>
        </div>
      </header>
      <div className={styles.formGrid}>
        <label>
          Default Session access
          <select
            value={accessPreset ?? ""}
            onChange={(event) => {
              const value = event.target.value;
              setAccessPresetDraft(
                value ? (value as ExecutionAccessPreset) : null,
              );
            }}
          >
            <option value="">
              {scope === "project"
                ? "Inherit user default"
                : `Use resolved fallback · ${settingsDefaultAccessLabel(settings)}`}
            </option>
            {ACCESS_PRESET_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className={styles.note}>
        Effective default: {settingsDefaultAccessLabel(settings)} · source:{" "}
        {settings?.resolvedDefaultSecuritySource.replaceAll("_", " ") ??
          "loading"}
        . Ask and Read only retain the workspace sandbox and protected-path
        guards. Full access removes those execution boundaries after
        confirmation. Low-level compatibility settings are available only
        through advanced configuration.
      </p>
      <footer className={styles.formActions}>
        <span>
          Sessions with their own access selection keep that override. New and
          inherited Sessions use this default.
        </span>
        <button
          className={styles.primaryButton}
          type="button"
          disabled={busy || accessPresetDraft === undefined}
          onClick={() => void saveSecurity()}
        >
          Save safety settings
        </button>
      </footer>
    </section>
  );
}
