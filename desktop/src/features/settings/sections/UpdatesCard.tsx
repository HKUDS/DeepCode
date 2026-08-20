/** Application updates — extracted from the old SettingsPage unchanged. */

import { Download, Rocket } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  DesktopRuntime,
  DesktopUpdateInfo,
  DesktopUpdateProgress,
} from "../../../rpc/contracts";
import styles from "../../management/ManagementWorkspace.module.css";

type UpdateState = "idle" | "checking" | "current" | "available" | "installing";

export function UpdatesCard({ runtime }: { runtime: DesktopRuntime }) {
  const { t } = useTranslation();
  const [updateInfo, setUpdateInfo] = useState<DesktopUpdateInfo | null>(null);
  const [updateState, setUpdateState] = useState<UpdateState>("idle");
  const [updateProgress, setUpdateProgress] =
    useState<DesktopUpdateProgress | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);

  const checkForUpdate = async () => {
    setUpdateState("checking");
    setUpdateInfo(null);
    setUpdateProgress(null);
    setUpdateError(null);
    try {
      const update = await runtime.checkForUpdate();
      setUpdateInfo(update);
      setUpdateState(update ? "available" : "current");
    } catch (cause) {
      setUpdateState("idle");
      setUpdateError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const installUpdate = async () => {
    setUpdateState("installing");
    setUpdateProgress(null);
    setUpdateError(null);
    try {
      await runtime.installUpdate(setUpdateProgress);
    } catch (cause) {
      setUpdateState("available");
      setUpdateError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <section className={styles.section}>
      <header className={styles.sectionHeader}>
        <div>
          <p className={styles.eyebrow}>{t("settings.updates.eyebrow", "Signed release channel")}</p>
          <h2>{t("settings.updates.title", "Application updates")}</h2>
        </div>
        <div className={styles.headerActions}>
          {updateInfo ? (
            <button
              className={styles.primaryButton}
              type="button"
              disabled={updateState === "installing"}
              onClick={() => void installUpdate()}
            >
              <Download size={14} />
              {updateState === "installing"
                ? updateProgressLabel(updateProgress, t)
                : `${t("settings.updates.install", "Install")} ${updateInfo.version}`}
            </button>
          ) : null}
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={updateState === "checking" || updateState === "installing"}
            onClick={() => void checkForUpdate()}
          >
            <Rocket size={14} />
            {updateState === "checking" ? t("settings.updates.checking", "Checking…") : t("settings.updates.check", "Check for updates")}
          </button>
        </div>
      </header>
      {updateError ? <p className={styles.errorBanner}>{updateError}</p> : null}
      <p className={styles.note}>
        {updateStatusMessage(updateState, updateInfo, updateProgress, t)}
      </p>
      {updateInfo?.body ? <p>{updateInfo.body}</p> : null}
    </section>
  );
}

function updateProgressLabel(
  progress: DesktopUpdateProgress | null,
  t: (key: string, defaultValue: string) => string,
): string {
  if (!progress || progress.phase === "started") return t("settings.updates.preparing", "Preparing…");
  if (progress.phase === "finished") return t("settings.updates.installing", "Installing…");
  if (!progress.totalBytes) return t("settings.updates.downloading", "Downloading…");
  const percentage = Math.min(
    100,
    Math.round((progress.downloadedBytes / progress.totalBytes) * 100),
  );
  return `${t("settings.updates.downloading", "Downloading…").replace("…", "")} ${percentage}%`;
}

function updateStatusMessage(
  state: UpdateState,
  update: DesktopUpdateInfo | null,
  progress: DesktopUpdateProgress | null,
  t: (key: string, defaultValue: string, options?: Record<string, string>) => string,
): string {
  if (state === "checking")
    return t("settings.updates.checkingNote", "Checking the configured signed release channel.");
  if (state === "current") return t("settings.updates.upToDate", "This installation is up to date.");
  if (state === "available" && update) {
    return t("settings.updates.available", "DeepCode {{version}} is available. The package signature is verified before installation.", { version: update.version });
  }
  if (state === "installing") return updateProgressLabel(progress, t);
  return t("settings.updates.idle", "Updates are checked only when requested. Development builds may not configure a release channel.");
}
