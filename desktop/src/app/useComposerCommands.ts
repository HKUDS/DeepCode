import { useCallback } from "react";

import type { ComposerCommand } from "../features/execution/commands";
import type { DesktopUiController } from "./useDesktopUi";
import type { WorkspaceController } from "./useWorkspaceController";

export function useComposerCommands(
  controller: WorkspaceController,
  ui: DesktopUiController,
) {
  return useCallback(
    async (command: ComposerCommand) => {
      switch (command.type) {
        case "new":
          if (!(await ui.confirmDiscardInspectorDraft())) return false;
          await controller.createThread();
          return true;
        case "paper":
          if (!(await ui.confirmDiscardInspectorDraft())) return false;
          await controller.createThread("paper");
          return true;
        case "review":
          ui.openInspector("changes");
          return true;
        case "fork":
          if (!(await ui.confirmDiscardInspectorDraft())) return false;
          await controller.forkThread();
          return true;
        case "rename":
          if (controller.selectedThread) {
            await controller.renameThread(
              controller.selectedThread.id,
              command.title,
            );
          }
          return true;
        case "model":
          await controller.setThreadModel(command.model);
          return true;
        case "permission":
          await controller.setPermissionMode(command.mode);
          return true;
      }
    },
    [controller, ui],
  );
}
