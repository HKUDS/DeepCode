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
  ): Promise<boolean>;
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
  const defaultAccessLabel = settingsDefaultAccessLabel(settings, t);
  const source = settings?.resolvedDefaultSecuritySource;
  const ignored = settings?.ignoredProjectSecurity ?? [];

  const saveSecurity = async () => {
    if (
      accessPreset === "full_access" &&
      !(await confirmAction(
        t(
          "settings.permissions.fullAccessConfirm",
          "Full access becomes the default for Sessions without an override. Tools may run without approval and outside the workspace sandbox.",
        ),
        {
          title: t(
            "settings.permissions.fullAccessTitle",
            "Use Full access by default?",
          ),
          kind: "warning",
          confirmLabel: t(
            "settings.permissions.fullAccessSave",
            "Save Full access default",
          ),
          cancelLabel: t("settings.permissions.cancel", "Cancel"),
        },
      ))
    ) {
      return;
    }
    const saved = await onUpdate(
      { security: { accessPreset } },
      scope,
      accessPreset === "full_access",
    );
    if (saved) setAccessPresetDraft(undefined);
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
          {t("settings.permissions.label", "Default Session access")}
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
                ? t("settings.permissions.inherit", "Inherit user default")
                : t(
                    "settings.permissions.fallback",
                    "Use resolved fallback · {{label}}",
                    { label: defaultAccessLabel },
                  )}
            </option>
            {ACCESS_PRESET_OPTIONS.map((option) => {
              // A project's settings file comes with its repository, so it
              // cannot make Full access the default (the server refuses it).
              const userOnly =
                scope === "project" && option.value === "full_access";
              const label = t(`access.preset.${option.value}`, option.label);
              return (
                <option
                  key={option.value}
                  value={option.value}
                  disabled={userOnly}
                >
                  {userOnly
                    ? t(
                        "settings.permissions.fullAccessUserOnly",
                        "{{label}} · user settings or one Session only",
                        { label },
                      )
                    : label}
                </option>
              );
            })}
          </select>
        </label>
      </div>
      <p className={styles.note}>
        {t(
          "settings.permissions.effective",
          "Effective default: {{label}} · source: {{source}}. Ask and Read only retain the workspace sandbox and protected-path guards. Full access removes those execution boundaries after confirmation. Low-level compatibility settings are available only through advanced configuration.",
          {
            label: defaultAccessLabel,
            source: source
              ? t(
                  `settings.permissions.source.${source}`,
                  source.replaceAll("_", " "),
                )
              : t("settings.permissions.source.loading", "loading"),
          },
        )}
      </p>
      {ignored.length > 0 ? (
        <p className={styles.note} role="status">
          {t(
            "settings.permissions.ignored",
            "This project's deepcode_config.json asks for {{what}}. DeepCode ignores that: a project's settings file comes with its repository. Choose Full access in user settings or for one Session.",
            {
              what: ignored
                .map((name) =>
                  name === "accessPreset"
                    ? t("settings.permissions.ignored.accessPreset", "Full access")
                    : t(
                        "settings.permissions.ignored.sandbox",
                        "the sandbox off",
                      ),
                )
                .join(t("settings.permissions.ignored.and", " and ")),
            },
          )}
        </p>
      ) : null}
      <footer className={styles.formActions}>
        <span>
          {t(
            "settings.permissions.overrideNote",
            "Sessions with their own access selection keep that override. New and inherited Sessions use this default.",
          )}
        </span>
        <button
          className={styles.primaryButton}
          type="button"
          disabled={busy || accessPresetDraft === undefined}
          onClick={() => void saveSecurity()}
        >
          {t("settings.permissions.save", "Save safety settings")}
        </button>
      </footer>
    </section>
  );
}
