/**
 * Settings dialog — the dsh settings-chrome adaptation: a centered modal
 * with a left navigation rail, a content column, and a header carrying the
 * two document-level actions (where writes go, and opening the config file
 * itself). Sections come from `SETTINGS_SECTIONS`; the shell owns zero
 * section copy.
 */

import { FileText, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  ConfigScope,
  JsonObject,
  Project,
  SettingsSnapshot,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import {
  SETTINGS_SECTIONS,
  type SettingsSectionId,
} from "./settingsSections";
import styles from "./SettingsDialog.module.css";

interface SettingsDialogProps {
  runtime: DesktopRuntime;
  project: Project | null;
  settings: SettingsSnapshot | null;
  busy: boolean;
  onRefresh(): Promise<void>;
  onUpdate(
    patch: JsonObject,
    scope: ConfigScope,
    riskAcknowledged?: boolean,
  ): Promise<void>;
  onClose(): void;
}

export function SettingsDialog({
  runtime,
  project,
  settings,
  busy,
  onRefresh,
  onUpdate,
  onClose,
}: SettingsDialogProps) {
  const { t } = useTranslation();
  const [activeId, setActiveId] = useState<SettingsSectionId>(
    SETTINGS_SECTIONS[0].id,
  );
  const [scope, setScope] = useState<ConfigScope>("user");
  const [openError, setOpenError] = useState<string | null>(null);
  const canWriteProject = project?.trustState === "trusted";
  const effectiveScope: ConfigScope =
    scope === "project" && canWriteProject ? "project" : "user";
  const active =
    SETTINGS_SECTIONS.find((section) => section.id === activeId) ??
    SETTINGS_SECTIONS[0];
  const ActiveSection = active.component;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    // The server pushes settings.changed when the config file moves under
    // us (another window, an external editor). Refreshing only while the
    // dialog is mounted is the dsh refresh-if-loaded rule: an unopened
    // surface never fetches on background invalidations.
    let disposed = false;
    let unsubscribe: (() => void) | null = null;
    void runtime
      .onNotification((note) => {
        if (note.method === "settings.changed") void onRefresh();
      })
      .then((dispose) => {
        if (disposed) dispose();
        else unsubscribe = dispose;
      });
    return () => {
      disposed = true;
      unsubscribe?.();
    };
  }, [runtime, onRefresh]);

  const openConfigFile = async () => {
    if (!settings?.configPath) return;
    setOpenError(null);
    try {
      await runtime.openPath(settings.configPath);
    } catch (cause) {
      setOpenError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div
      className={styles.backdrop}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-dialog-title"
      >
        <header className={styles.header}>
          <h1 id="settings-dialog-title">{t("settings.title", "Settings")}</h1>
          <div className={styles.headerActions}>
            <label className={styles.scope}>
              {t("settings.writeTo", "Write to")}
              <select
                value={effectiveScope}
                onChange={(event) =>
                  setScope(event.target.value as ConfigScope)
                }
              >
                <option value="user">{t("settings.scope.user", "User config")}</option>
                <option value="project" disabled={!canWriteProject}>
                  {t("settings.scope.project", "Selected project")}
                </option>
              </select>
            </label>
            <button
              className={styles.documentAction}
              type="button"
              disabled={!settings?.configPath}
              title={settings?.configPath ?? undefined}
              onClick={() => void openConfigFile()}
            >
              <FileText size={14} />
              {t("settings.openConfig", "Open configuration file")}
            </button>
            <button
              className={styles.close}
              type="button"
              aria-label={t("settings.close", "Close settings")}
              onClick={onClose}
            >
              <X size={16} />
            </button>
          </div>
        </header>
        {openError ? <p className={styles.openError}>{openError}</p> : null}
        <div className={styles.body}>
          <nav className={styles.rail} aria-label="Settings sections">
            {SETTINGS_SECTIONS.map((section) => {
              const Icon = section.icon;
              return (
                <button
                  key={section.id}
                  type="button"
                  className={styles.railButton}
                  data-active={section.id === active.id}
                  onClick={() => setActiveId(section.id)}
                >
                  <Icon size={16} />
                  {t(section.labelKey, section.label)}
                </button>
              );
            })}
          </nav>
          <div className={styles.content}>
            <ActiveSection
              runtime={runtime}
              project={project}
              settings={settings}
              busy={busy}
              scope={effectiveScope}
              onRefresh={onRefresh}
              onUpdate={onUpdate}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
