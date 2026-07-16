import { AlertTriangle, RotateCw, X } from "lucide-react";

import type { BridgeError, SidecarStatus } from "../rpc/contracts";
import styles from "./RuntimeNotice.module.css";

interface RuntimeNoticeProps {
  runtime: SidecarStatus;
  error: BridgeError | null;
  busy: boolean;
  onRestart(): void;
  onDismissError(): void;
}

export function RuntimeNotice({
  runtime,
  error,
  busy,
  onRestart,
  onDismissError,
}: RuntimeNoticeProps) {
  if (!error && runtime.phase !== "crashed" && runtime.phase !== "stopped") return null;
  const serviceOffline = runtime.phase === "crashed" || runtime.phase === "stopped";
  const message = error?.message ?? runtime.message ?? "The local App Server is unavailable.";
  return (
    <div className={styles.notice} role="alert">
      <AlertTriangle size={17} aria-hidden="true" />
      <div className={styles.copy}>
        <strong>{error?.code ?? "APP_SERVER_OFFLINE"}</strong>
        <span>{message}</span>
      </div>
      <div className={styles.actions}>
        {error ? (
          <button
            className={styles.dismiss}
            type="button"
            onClick={onDismissError}
            aria-label="Dismiss error"
          >
            <X size={14} />
          </button>
        ) : null}
        {serviceOffline ? (
          <button type="button" onClick={onRestart} disabled={busy}>
            <RotateCw size={13} />
            Restart service
          </button>
        ) : null}
      </div>
    </div>
  );
}
