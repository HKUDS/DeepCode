import type { Project } from "../generated/app-server";

const MISSING_WORKSPACE_SEGMENT = "/.deepcode/sessions/.missing-workspaces/";

function normalizedPath(path: string): string {
  return path.replaceAll("\\", "/");
}

export function isRecoveredHistoryProject(
  project: Project | null | undefined,
): boolean {
  return Boolean(
    project &&
      normalizedPath(project.canonicalPath).includes(
        MISSING_WORKSPACE_SEGMENT,
      ),
  );
}

export function projectCanExecute(
  project: Project | null | undefined,
): boolean {
  return Boolean(project && !isRecoveredHistoryProject(project));
}
