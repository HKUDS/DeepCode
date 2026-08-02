import type {
  ConfigScope,
  JsonObject,
  Project,
  SettingsSnapshot,
  Thread,
} from "../../generated/app-server";
import type { DesktopDestination } from "../../app/useDesktopUi";
import type { DesktopRuntime } from "../../rpc/contracts";
import { AutomationsPage } from "../automations/AutomationsPage";
import { SkillsPage } from "../extensions/SkillsPage";
import { SettingsPage } from "../settings/SettingsPage";

interface ManagementWorkspaceProps {
  destination: DesktopDestination;
  runtime: DesktopRuntime;
  project: Project | null;
  settings: SettingsSnapshot | null;
  busy: boolean;
  onRefreshSettings(): Promise<void>;
  onUpdateSettings(
    patch: JsonObject,
    scope: ConfigScope,
    riskAcknowledged?: boolean,
  ): Promise<void>;
  onThreadCreated(thread: Thread): void;
  onOpenThread(threadId: string): void;
}

export function ManagementWorkspace({
  destination,
  runtime,
  project,
  settings,
  busy,
  onRefreshSettings,
  onUpdateSettings,
  onThreadCreated,
  onOpenThread,
}: ManagementWorkspaceProps) {
  if (destination === "automations") {
    return (
      <AutomationsPage
        key={project?.id ?? "global"}
        runtime={runtime}
        project={project}
        onThreadCreated={onThreadCreated}
        onOpenThread={onOpenThread}
      />
    );
  }
  if (destination === "skills") {
    return <SkillsPage runtime={runtime} project={project} />;
  }
  return (
    <SettingsPage
      key={project?.id ?? "global"}
      runtime={runtime}
      project={project}
      settings={settings}
      busy={busy}
      onRefresh={onRefreshSettings}
      onUpdate={onUpdateSettings}
    />
  );
}
