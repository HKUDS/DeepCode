/**
 * Agent presets — the roster of compositions this deployment can start a
 * Session with. Presets are selected per Session (composer) and snapshotted
 * by value at selection; this section is the directory view of what exists,
 * with trust provenance made visible.
 */

import { useEffect, useState } from "react";

import type { AgentPresetEntry } from "../../../generated/app-server";
import type { SettingsSectionProps } from "../settingsSections";
import styles from "../../management/ManagementWorkspace.module.css";

export function PresetsSection({ runtime, project }: SettingsSectionProps) {
  const projectId = project?.id ?? null;
  const [roster, setRoster] = useState<{
    projectId: string;
    entries: AgentPresetEntry[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    let stale = false;
    runtime
      .request("preset/list", { projectId })
      .then((result) => {
        if (!stale) setRoster({ projectId, entries: result.presets });
      })
      .catch((cause) => {
        if (!stale) {
          setRoster({ projectId, entries: [] });
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      });
    return () => {
      stale = true;
    };
  }, [projectId, runtime]);

  const entries = roster?.projectId === projectId ? roster.entries : [];

  return (
    <section className={styles.section}>
      <header className={styles.sectionHeader}>
        <div>
          <p className={styles.eyebrow}>Session compositions</p>
          <h2>Agent presets</h2>
        </div>
      </header>
      <p className={styles.note}>
        A preset bundles a system prompt and tool policy. Pick one per Session
        from the composer before the conversation starts; the selection is
        snapshotted into the Session so later edits to the preset file never
        change a running conversation.
      </p>
      {!projectId ? (
        <p className={styles.note}>Open a project to list its presets.</p>
      ) : error ? (
        <p className={styles.errorBanner}>{error}</p>
      ) : !entries.length ? (
        <p className={styles.note}>
          No presets discovered. Add markdown presets under{" "}
          <code>.deepcode/agents/</code> in the project or your home directory.
        </p>
      ) : (
        <div className={styles.checkList}>
          {entries.map((entry) => (
            <article key={entry.id} data-status={entry.broken ? "warn" : "ok"}>
              <span />
              <div>
                <strong>
                  {entry.name} <small>[{entry.trust}]</small>
                </strong>
                <p>{entry.broken ?? (entry.description || "No description.")}</p>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
