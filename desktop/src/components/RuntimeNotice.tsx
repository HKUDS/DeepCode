import { AlertTriangle, RotateCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { BridgeError, SidecarStatus } from "../rpc/contracts";
import styles from "./RuntimeNotice.module.css";
import { runtimeNoticeText } from "./runtimeNoticeText";

interface RuntimeNoticeProps {
  runtime: SidecarStatus;
  error: BridgeError | null;
  busy: boolean;
  onRestart(): void;
  onDismissError(): void;
  browserHost?: boolean;
}

export function RuntimeNotice({
  runtime,
  error,
  busy,
  onRestart,
  onDismissError,
  browserHost = false,
}: RuntimeNoticeProps) {
  const { t } = useTranslation();
  if (!error && runtime.phase !== "crashed" && runtime.phase !== "stopped") return null;
  const serviceOffline = runtime.phase === "crashed" || runtime.phase === "stopped";
  const code = runtime.errorCode ?? error?.code;
  const authRequired = browserHost && code === "AUTH_REQUIRED";
  const serviceMessage =
    (runtime.errorCode ? runtime.message : error?.message) ??
    runtime.message ??
    t("runtime.offline", "The local App Server is unavailable.");
  const [title, message] = runtimeNoticeText(
    code,
    serviceMessage,
    { serviceOffline, browser: browserHost },
    t,
  );
  return (
    <div className={styles.notice} role="alert">
      <AlertTriangle size={18} aria-hidden="true" />
      <div className={styles.copy}>
        <strong>{title}</strong>
        <span>{message}</span>
      </div>
      <div className={styles.actions}>
        {error && !authRequired ? (
          <button
            className={styles.dismiss}
            type="button"
            onClick={onDismissError}
            aria-label={t("runtime.dismissError", "Dismiss error")}
          >
            <X size={14} />
          </button>
        ) : null}
        {serviceOffline && !authRequired ? (
          <button type="button" onClick={onRestart} disabled={busy}>
            <RotateCw size={14} />
            {browserHost || runtime.serverInfo?.serviceInfo?.shutdownScope === "connection"
              ? t("runtime.reconnect", "Reconnect")
              : t("runtime.restart", "Restart service")}
          </button>
        ) : null}
      </div>
    </div>
  );
}
