import { AlertTriangle, RotateCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { BridgeError, SidecarStatus } from "../rpc/contracts";
import styles from "./RuntimeNotice.module.css";

interface RuntimeNoticeProps {
  runtime: SidecarStatus;
  error: BridgeError | null;
  busy: boolean;
  onRestart(): void;
  onDismissError(): void;
  reconnectOnly?: boolean;
}

export function RuntimeNotice({
  runtime,
  error,
  busy,
  onRestart,
  onDismissError,
  reconnectOnly = false,
}: RuntimeNoticeProps) {
  const { t } = useTranslation();
  if (!error && runtime.phase !== "crashed" && runtime.phase !== "stopped") return null;
  const serviceOffline = runtime.phase === "crashed" || runtime.phase === "stopped";
  const code = runtime.errorCode ?? error?.code;
  const authRequired = reconnectOnly && code === "AUTH_REQUIRED";
  const message = authRequired
    ? t("runtime.browserAuthHelp", "Run deepcode web in your terminal to open a new browser access link. No DeepCode account is needed.")
    : (runtime.errorCode ? runtime.message : error?.message) ?? runtime.message ?? t("runtime.offline", "The local App Server is unavailable.");
  return (
    <div className={styles.notice} role="alert">
      <AlertTriangle size={18} aria-hidden="true" />
      <div className={styles.copy}>
        <strong>{authRequired ? t("runtime.browserAuthRequired", "Browser access required") : code ?? "APP_SERVER_OFFLINE"}</strong>
        <span>{message}</span>
      </div>
      <div className={styles.actions}>
        {error && !authRequired ? (
          <button
            className={styles.dismiss}
            type="button"
            onClick={onDismissError}
            aria-label="Dismiss error"
          >
            <X size={14} />
          </button>
        ) : null}
        {serviceOffline && !authRequired ? (
          <button type="button" onClick={onRestart} disabled={busy}>
            <RotateCw size={14} />
            {reconnectOnly || runtime.serverInfo?.serviceInfo?.shutdownScope === "connection"
              ? t("runtime.reconnect", "Reconnect")
              : t("runtime.restart", "Restart service")}
          </button>
        ) : null}
      </div>
    </div>
  );
}
