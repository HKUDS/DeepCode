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
import { useTranslation } from "react-i18next";

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

function workspaceLabel(thread: Thread | null, project: Project | null, t: (key: string, defaultValue: string) => string): string {
  if (isRecoveredHistoryProject(project)) return t("thread.folderUnavailable", "Folder unavailable");
  const path = thread?.workspacePath ?? project?.canonicalPath;
  if (!path) return t("thread.noLocalFolder", "No local folder");
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
  const { t } = useTranslation();
  const recoveredHistory = isRecoveredHistoryProject(project);
  const title = thread?.title ?? t("thread.startThread", "Start a local coding thread");
  const root = recoveredHistory
    ? t("thread.previousSessions", "Previous sessions")
    : (project?.displayName ?? "DeepCode");
  const workspace = thread ? workspaceLabel(thread, project, t) : null;
  return (
    <header className={styles.header}>
      <div className={styles.identity}>
        <div className={styles.breadcrumb}>
          <span>{root}</span>
          {/* A Session usually opens at the project root, and then both
              segments are the same folder name — "Test_workbench /
              Test_workbench" reads as a bug. The trail only earns its second
              step when the Session actually sits somewhere else. */}
          {workspace && workspace !== root ? (
            <>
              <span aria-hidden="true">/</span>
              <span>{workspace}</span>
            </>
          ) : null}
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
            title={t("thread.folderUnavailableTooltip", "The original Session folder is no longer available")}
          >
            <FolderX size={14} />
            {t("thread.folderUnavailable", "Folder unavailable")}
          </span>
        ) : project?.trustState === "untrusted" ? (
          <button
            className={styles.attentionAction}
            type="button"
            onClick={onTrustProject}
            disabled={busy}
          >
            <ShieldAlert size={16} />
            {t("thread.trustFolder", "Trust folder")}
          </button>
        ) : project ? (
          <span className={styles.trustState} title={t("thread.trustedTooltip", "Execution is allowed in this folder")}>
            <ShieldCheck size={14} />
            {t("thread.trusted", "Trusted")}
          </span>
        ) : null}

        {thread && project?.trustState === "trusted" && !recoveredHistory ? (
          <>
            {thread.mode !== "paper" ? (
              <button
                type="button"
                onClick={onCreatePaperThread}
                disabled={busy || hasActiveWork}
                title={t("thread.paperTooltip", "Create a Paper2Code thread")}
              >
                <ScrollText size={16} />
                {t("thread.paper", "Paper")}
              </button>
            ) : null}
            <button
              type="button"
              onClick={onForkThread}
              disabled={busy || hasActiveWork}
              title={t("thread.forkTooltip", "Fork into an isolated worktree")}
            >
              <GitFork size={16} />
              {t("thread.fork", "Fork")}
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
            title={inspectorOpen ? t("thread.closeReview", "Close review panel") : t("thread.openReview", "Open review panel")}
          >
            <Files size={16} />
            {t("thread.review", "Review")}
            {inspectorOpen ? <PanelRightClose size={14} /> : <PanelRight size={14} />}
          </button>
        ) : null}
      </div>
    </header>
  );
}
