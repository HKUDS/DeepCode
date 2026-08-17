/** Application updates — extracted from the old SettingsPage unchanged. */

import { Download, Rocket } from "lucide-react";
import { useState } from "react";

import type {
  DesktopRuntime,
  DesktopUpdateInfo,
  DesktopUpdateProgress,
} from "../../../rpc/contracts";
import styles from "../../management/ManagementWorkspace.module.css";

type UpdateState = "idle" | "checking" | "current" | "available" | "installing";

export function UpdatesCard({ runtime }: { runtime: DesktopRuntime }) {
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
          <p className={styles.eyebrow}>Signed release channel</p>
          <h2>Application updates</h2>
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
                ? updateProgressLabel(updateProgress)
                : `Install ${updateInfo.version}`}
            </button>
          ) : null}
          <button
            className={styles.secondaryButton}
            type="button"
            disabled={updateState === "checking" || updateState === "installing"}
            onClick={() => void checkForUpdate()}
          >
            <Rocket size={14} />
            {updateState === "checking" ? "Checking…" : "Check for updates"}
          </button>
        </div>
      </header>
      {updateError ? <p className={styles.errorBanner}>{updateError}</p> : null}
      <p className={styles.note}>
        {updateStatusMessage(updateState, updateInfo, updateProgress)}
      </p>
      {updateInfo?.body ? <p>{updateInfo.body}</p> : null}
    </section>
  );
}

function updateProgressLabel(progress: DesktopUpdateProgress | null): string {
  if (!progress || progress.phase === "started") return "Preparing…";
  if (progress.phase === "finished") return "Installing…";
  if (!progress.totalBytes) return "Downloading…";
  const percentage = Math.min(
    100,
    Math.round((progress.downloadedBytes / progress.totalBytes) * 100),
  );
  return `Downloading ${percentage}%`;
}

function updateStatusMessage(
  state: UpdateState,
  update: DesktopUpdateInfo | null,
  progress: DesktopUpdateProgress | null,
): string {
  if (state === "checking")
    return "Checking the configured signed release channel.";
  if (state === "current") return "This installation is up to date.";
  if (state === "available" && update) {
    return `DeepCode ${update.version} is available. The package signature is verified before installation.`;
  }
  if (state === "installing") return updateProgressLabel(progress);
  return "Updates are checked only when requested. Development builds may not configure a release channel.";
}
