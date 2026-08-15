/**
 * Models — provider connections (the directory + editor) above, the default
 * agent model below. Both halves share one connection-catalog controller so
 * a provider edit is immediately visible to the model picker.
 */

import { ConnectionSettings } from "../ConnectionSettings";
import { useConnectionCatalog } from "../useConnectionCatalog";
import type { SettingsSectionProps } from "../settingsSections";
import { AgentModelCard } from "./AgentModelCard";
import styles from "../../management/ManagementWorkspace.module.css";

export function ModelsSection({
  runtime,
  project,
  settings,
  busy,
  scope,
  onUpdate,
}: SettingsSectionProps) {
  const connections = useConnectionCatalog(runtime, project?.id ?? null);
  return (
    <div className={styles.settingsGrid}>
      <ConnectionSettings controller={connections} busy={busy} />
      <AgentModelCard
        settings={settings}
        busy={busy}
        scope={scope}
        connections={connections}
        onUpdate={onUpdate}
      />
    </div>
  );
}
