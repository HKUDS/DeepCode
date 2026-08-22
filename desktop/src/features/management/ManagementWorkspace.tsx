import type { Project, SkillInfo, Thread } from "../../generated/app-server";
import type { DesktopDestination } from "../../app/useDesktopUi";
import type { DesktopRuntime } from "../../rpc/contracts";
import { AutomationsPage } from "../automations/AutomationsPage";
import { SkillsPage } from "../extensions/SkillsPage";
import { PluginsPage } from "../plugins/PluginsPage";
import { McpPage } from "../mcp/McpPage";

interface ManagementWorkspaceProps {
  destination: DesktopDestination;
  runtime: DesktopRuntime;
  project: Project | null;
  onThreadCreated(thread: Thread): void;
  onOpenThread(threadId: string): void;
  onCreateSkill(skill: SkillInfo): Promise<void>;
}

export function ManagementWorkspace({
  destination,
  runtime,
  project,
  onThreadCreated,
  onOpenThread,
  onCreateSkill,
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
    return (
      <SkillsPage
        runtime={runtime}
        project={project}
        onCreateSkill={onCreateSkill}
      />
    );
  }
  if (destination === "plugins") {
    return <PluginsPage runtime={runtime} />;
  }
  return <McpPage runtime={runtime} project={project} />;
}
