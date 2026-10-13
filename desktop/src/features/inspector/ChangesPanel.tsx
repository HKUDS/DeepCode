import { useTranslation } from "react-i18next";

import type { Thread } from "../../generated/app-server";
import { confirmAction } from "../../platform/confirmAction";
import type { CodeWorkbenchController } from "../workbench/useCodeWorkbench";
import { InspectorEmpty } from "./InspectorEmpty";
import styles from "./Inspector.module.css";

interface ChangesPanelProps {
  thread: Thread | null;
  trusted: boolean;
  hasActiveTurn: boolean;
  workbench: CodeWorkbenchController;
  onOpenFile(path: string): void;
}

export function ChangesPanel({
  thread,
  trusted,
  hasActiveTurn,
  workbench,
  onOpenFile,
}: ChangesPanelProps) {
  const { t } = useTranslation();
  const cleanWorktree = async () => {
    const dirty = Boolean(workbench.git?.entries.length);
    if (
      dirty &&
      !(await confirmAction(
        t(
          "inspector.changes.cleanConfirm",
          "This worktree has uncommitted changes. Permanently remove it and discard them?",
        ),
        {
          confirmLabel: t("inspector.changes.removeWorktree", "Remove worktree"),
        },
      ))
    ) {
      return;
    }
    await workbench.resolveWorktree("clean", dirty);
  };

  const discardFile = async (path: string, revision: string) => {
    const file = workbench.diffs.find(
      (candidate) => candidate.path === path && candidate.revision === revision,
    );
    if (
      !file ||
      !(await confirmAction(
        t(
          "inspector.changes.discardConfirm",
          "Discard every staged and unstaged change to {{path}}? This cannot be undone.",
          { path },
        ),
        {
          confirmLabel: t("inspector.changes.discardChanges", "Discard changes"),
        },
      ))
    ) {
      return;
    }
    await workbench.discardChange(file);
  };

  return (
    <div className={styles.content}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>
            {t("inspector.changes.eyebrow", "Git workspace")}
          </p>
          <h2>
            {workbench.git?.branch ??
              t("inspector.changes.noBranch", "No Git branch")}
          </h2>
        </div>
        <button type="button" onClick={() => void workbench.refresh()}>
          {t("inspector.changes.refresh", "Refresh")}
        </button>
      </div>

      {thread?.worktreePath ? (
        <div className={styles.worktreeControl}>
          <strong>{t("inspector.changes.isolated", "Isolated worktree")}</strong>
          <span>{thread.worktreePath}</span>
          <div>
            <button
              type="button"
              disabled={!trusted || hasActiveTurn || workbench.loading}
              onClick={() => void workbench.resolveWorktree("keep")}
            >
              {t("inspector.changes.keepWorktree", "Keep worktree")}
            </button>
            <button
              type="button"
              disabled={!trusted || hasActiveTurn || workbench.loading}
              onClick={() => void cleanWorktree()}
            >
              {t("inspector.changes.cleanWorktree", "Clean worktree")}
            </button>
          </div>
        </div>
      ) : (
        <button
          className={styles.outlinedAction}
          type="button"
          onClick={() => void workbench.createWorktree()}
          disabled={
            !trusted || hasActiveTurn || !workbench.git || workbench.loading
          }
        >
          {t("inspector.changes.isolate", "Isolate this Session in a worktree")}
        </button>
      )}

      {workbench.gitError ? (
        <InspectorEmpty label={workbench.gitError} compact />
      ) : workbench.diffs.length ? (
        <div className={styles.diffList}>
          {workbench.diffs.map((file) => (
            <section key={file.path} className={styles.fileDiff}>
              <header>
                <strong>{file.path}</strong>
                <span>
                  <b>+{file.additions}</b> <i>−{file.deletions}</i>
                </span>
              </header>
              <div className={styles.diffActions}>
                <button
                  type="button"
                  disabled={file.binary || file.status === "deleted"}
                  onClick={() => onOpenFile(file.path)}
                >
                  {t("inspector.changes.openInEditor", "Open in editor")}
                </button>
                <button
                  type="button"
                  disabled={!trusted || hasActiveTurn || workbench.loading}
                  onClick={() => void discardFile(file.path, file.revision)}
                >
                  {t("inspector.changes.discardFile", "Discard file")}
                </button>
              </div>
              {file.binary ? (
                <p className={styles.binaryNotice}>
                  {t("inspector.changes.binary", "Binary file changed")}
                </p>
              ) : (
                file.hunks.map((hunk, hunkIndex) => (
                  <div className={styles.diffHunk} key={`${file.path}-${hunkIndex}`}>
                    <div className={styles.hunkHeader}>
                      −{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},{hunk.newLines}
                    </div>
                    {hunk.lines.map((line, lineIndex) => (
                      <div
                        className={styles.diffLine}
                        data-kind={line.kind}
                        key={`${line.kind}-${lineIndex}`}
                      >
                        <span>{line.oldLine ?? ""}</span>
                        <span>{line.newLine ?? ""}</span>
                        <code>
                          {line.kind === "addition"
                            ? "+"
                            : line.kind === "deletion"
                              ? "−"
                              : " "}
                          {line.text}
                        </code>
                      </div>
                    ))}
                  </div>
                ))
              )}
            </section>
          ))}
        </div>
      ) : (
        <InspectorEmpty
          label={t("inspector.changes.clean", "Working tree is clean.")}
          compact
        />
      )}
      {hasActiveTurn ? (
        <p className={styles.note}>
          {t(
            "inspector.changes.locked",
            "File changes and worktree operations are locked while a Turn is active.",
          )}
        </p>
      ) : null}
    </div>
  );
}
