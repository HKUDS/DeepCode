import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ClientRuntime } from "../../../rpc/contracts";
import { confirmAction } from "../../../platform/confirmAction";
import styles from "../../management/ManagementWorkspace.module.css";

type Activity = {
  phase: string;
  activeTurns: number;
  queuedTurns: number;
  terminals: number;
};

export function ServiceCard({ runtime }: { runtime: ClientRuntime }) {
  const { t } = useTranslation();
  const [shared, setShared] = useState(false);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    if (!runtime.serviceStatus || !runtime.stopService) return;
    void runtime
      .status()
      .then(async (status) => {
        if (
          cancelled ||
          status.serverInfo?.serviceInfo?.shutdownScope !== "connection"
        )
          return;
        setShared(true);
        const current = await runtime.serviceStatus!();
        if (!cancelled) setActivity(current);
      })
      .catch((cause) => {
        if (!cancelled) setError(String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [runtime]);
  if (!shared) return null;
  const stop = async () => {
    setBusy(true);
    setError(null);
    try {
      const current = await runtime.serviceStatus!();
      setActivity(current);
      if (
        !(await confirmAction(
          t(
            "service.stopConfirm",
            "Stop the shared service? {{active}} active tasks, {{queued}} queued tasks, {{terminals}} terminals. It will wait up to 10 seconds; if work remains active, the service stays running.",
            {
              active: current.activeTurns,
              queued: current.queuedTurns,
              terminals: current.terminals,
            },
          ),
          { confirmLabel: t("service.stop", "Stop background service") },
        ))
      )
        return;
      await runtime.stopService!();
      setActivity({ ...current, phase: "stopped" });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className={styles.section}>
      <header className={styles.sectionHeader}>
        <h2>{t("service.title", "Background service")}</h2>
        <button
          className={styles.secondaryButton}
          disabled={busy || activity?.phase === "stopped"}
          onClick={() => void stop()}
        >
          {t("service.stop", "Stop background service")}
        </button>
      </header>
      <p className={styles.note}>
        {t(
          "service.detach",
          "Closing Desktop disconnects this window. Tasks and scheduled work continue in the shared service.",
        )}
      </p>
      {activity && (
        <p>
          {t(
            "service.activity",
            "{{phase}} · {{active}} active tasks · {{queued}} queued · {{terminals}} terminals",
            {
              phase: activity.phase,
              active: activity.activeTurns,
              queued: activity.queuedTurns,
              terminals: activity.terminals,
            },
          )}
        </p>
      )}
      {error && (
        <p className={styles.errorBanner} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
