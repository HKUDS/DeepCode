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
  const serviceMessage =
    (runtime.errorCode ? runtime.message : error?.message) ??
    runtime.message ??
    t("runtime.offline", "The local App Server is unavailable.");
  // What the browser's own connection reports is said here, in the user's
  // language; anything else is the service's own message.
  const [title, message] = authRequired
    ? [
        t("runtime.browserAuthRequired", "Browser access required"),
        t("runtime.browserAuthHelp", "Run deepcode web in your terminal to open a new browser access link. No DeepCode account is needed."),
      ]
    : code === "RESPONSE_TIMEOUT"
      ? [
          t("runtime.responseTimeout", "No response from DeepCode"),
          t("runtime.responseTimeoutHelp", "The response timed out; the task may still be running."),
        ]
      : code === "CONNECTION_LOST"
        ? [
            t("runtime.connectionLost", "Connection to DeepCode lost"),
            serviceOffline
              ? t("runtime.connectionLostOffline", "The service is unavailable. Reconnect when it is ready.")
              : t("runtime.connectionLostHelp", "Connection lost; the service may still be running."),
          ]
        : [
            serviceOffline
              ? t("runtime.serviceUnavailable", "Local service unavailable")
              : t("runtime.requestFailed", "Request failed"),
            serviceMessage,
          ];
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
            {reconnectOnly || runtime.serverInfo?.serviceInfo?.shutdownScope === "connection"
              ? t("runtime.reconnect", "Reconnect")
              : t("runtime.restart", "Restart service")}
          </button>
        ) : null}
      </div>
    </div>
  );
}
