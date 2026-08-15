/**
 * General — the dsh General page adapted: session defaults first
 * (permissions; the default agent preset joins in the presets section),
 * machine-local appearance next, application maintenance last.
 */

import { AppearanceSettings } from "../AppearanceSettings";
import type { SettingsSectionProps } from "../settingsSections";
import { DiagnosticsCard } from "./DiagnosticsCard";
import { PermissionCard } from "./PermissionCard";
import { UpdatesCard } from "./UpdatesCard";
import styles from "../../management/ManagementWorkspace.module.css";

export function GeneralSection({
  runtime,
  project,
  settings,
  busy,
  scope,
  onUpdate,
}: SettingsSectionProps) {
  return (
    <div className={styles.settingsGrid}>
      <PermissionCard
        settings={settings}
        busy={busy}
        scope={scope}
        onUpdate={onUpdate}
      />
      <AppearanceSettings />
      <UpdatesCard runtime={runtime} />
      <DiagnosticsCard runtime={runtime} project={project} />
      <aside className={styles.credits} aria-label="Visual credits">
        <span className={styles.creditMark} aria-hidden="true" />
        <p>
          <strong>Visual credits</strong>
          <small>
            Selected outline accents designed by The Icon Tree from Flaticon.
            Plugins and Skills empty-state marks from Phosphor Icons (MIT).
          </small>
        </p>
      </aside>
    </div>
  );
}
