import {
  Files,
  FolderX,
  GitFork,
  PanelRight,
  PanelRightClose,
  ScrollText,
  ShieldCheck,
  ShieldAlert,
} from "lucide-react";

import { isRecoveredHistoryProject } from "../../app/projectPresentation";
import type { Project, Thread } from "../../generated/app-server";
import type { SidecarStatus } from "../../rpc/contracts";
import styles from "./ThreadHeader.module.css";

interface ThreadHeaderProps {
  project: Project | null;
  thread: Thread | null;
  runtime: SidecarStatus;
  busy: boolean;
  inspectorOpen: boolean;
  hasActiveWork: boolean;
  onTrustProject(): void;
  onForkThread(): void;
  onCreatePaperThread(): void;
  onToggleInspector(): void;
}

function workspaceLabel(thread: Thread | null, project: Project | null): string {
  if (isRecoveredHistoryProject(project)) return "Folder unavailable";
  const path = thread?.workspacePath ?? project?.canonicalPath;
  if (!path) return "No local folder";
  const parts = path.split("/").filter(Boolean);
  return parts.at(-1) ?? path;
}

export function ThreadHeader({
  project,
  thread,
  runtime,
  busy,
  inspectorOpen,
  hasActiveWork,
  onTrustProject,
  onForkThread,
  onCreatePaperThread,
  onToggleInspector,
}: ThreadHeaderProps) {
  const recoveredHistory = isRecoveredHistoryProject(project);
  const title = thread?.title ?? "Start a local coding thread";
  return (
    <header className={styles.header}>
      <div className={styles.identity}>
        <div className={styles.breadcrumb}>
          <span>
            {recoveredHistory
              ? "Previous sessions"
              : (project?.displayName ?? "DeepCode")}
          </span>
          {thread ? <span aria-hidden="true">/</span> : null}
          {thread ? <span>{workspaceLabel(thread, project)}</span> : null}
        </div>
        <div className={styles.titleRow}>
          <span
            className={styles.beacon}
            data-status={thread?.status ?? runtime.phase}
            aria-hidden="true"
          />
          <h1 id="thread-title" title={title}>
            {title}
          </h1>
        </div>
      </div>

      <div className={styles.actions}>
        {recoveredHistory ? (
          <span
            className={styles.unavailableState}
            title="The original Session folder is no longer available"
          >
            <FolderX size={14} />
            Folder unavailable
          </span>
        ) : project?.trustState === "untrusted" ? (
          <button
            className={styles.attentionAction}
            type="button"
            onClick={onTrustProject}
            disabled={busy}
          >
            <ShieldAlert size={15} />
            Trust folder
          </button>
        ) : project ? (
          <span className={styles.trustState} title="Execution is allowed in this folder">
            <ShieldCheck size={14} />
            Trusted
          </span>
        ) : null}

        {thread && project?.trustState === "trusted" && !recoveredHistory ? (
          <>
            {thread.mode !== "paper" ? (
              <button
                type="button"
                onClick={onCreatePaperThread}
                disabled={busy || hasActiveWork}
                title="Create a Paper2Code thread"
              >
                <ScrollText size={15} />
                Paper
              </button>
            ) : null}
            <button
              type="button"
              onClick={onForkThread}
              disabled={busy || hasActiveWork}
              title="Fork into an isolated worktree"
            >
              <GitFork size={15} />
              Fork
            </button>
          </>
        ) : null}

        {thread ? (
          <button
            className={styles.reviewAction}
            type="button"
            data-active={inspectorOpen}
            onClick={onToggleInspector}
            aria-pressed={inspectorOpen}
            title={inspectorOpen ? "Close review panel" : "Open review panel"}
          >
            <Files size={15} />
            Review
            {inspectorOpen ? <PanelRightClose size={14} /> : <PanelRight size={14} />}
          </button>
        ) : null}
      </div>
    </header>
  );
}
