import { lazy, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";

import { useSystemDarkMode } from "../../app/useSystemDarkMode";
import type { CodeWorkbenchController } from "../workbench/useCodeWorkbench";
import { InspectorEmpty } from "./InspectorEmpty";
import { languageFor } from "./inspectorFormat";
import styles from "./Inspector.module.css";

const LocalMonacoEditor = lazy(() => import("../workbench/LocalMonacoEditor"));

interface FilesPanelProps {
  trusted: boolean;
  hasActiveTurn: boolean;
  workbench: CodeWorkbenchController;
  onOpenFile(path: string): Promise<void>;
  onDownload?: (path: string) => Promise<void>;
}

export function FilesPanel({
  trusted,
  hasActiveTurn,
  workbench,
  onOpenFile,
  onDownload,
}: FilesPanelProps) {
  const { t } = useTranslation();
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const darkMode = useSystemDarkMode();
  const dirty = Boolean(
    workbench.file && workbench.draft !== workbench.file.content,
  );

  return (
    <div className={styles.filesView}>
      <div
        className={styles.fileTree}
        aria-label={t("inspector.files.label", "Workspace files")}
      >
        {workbench.entries.map((entry) => (
          <button
            key={entry.path}
            type="button"
            disabled={entry.kind !== "file"}
            onClick={() => void onOpenFile(entry.path)}
            style={{ paddingLeft: `${10 + (entry.path.split("/").length - 1) * 10}px` }}
          >
            <span aria-hidden="true">{entry.kind === "directory" ? "▾" : "·"}</span>
            {entry.name}
          </button>
        ))}
        {workbench.entriesTruncated ? (
          <p className={styles.treeNotice}>
            {t(
              "inspector.files.truncated",
              "File list limited to the first 750 entries.",
            )}
          </p>
        ) : null}
      </div>
      {workbench.file ? (
        <div className={styles.fileEditor}>
          <header>
            <span title={workbench.file.path}>{workbench.file.path}</span>
            <div>
              {onDownload && <button type="button" onClick={() => {
                setDownloadError(null);
                void onDownload(workbench.file!.path).catch((error) => setDownloadError(String(error)));
              }}>{t("inspector.files.download", "Download")}</button>}
              {workbench.file.truncated ? (
                <small>
                  {t("inspector.files.truncatedReadOnly", "Truncated · read-only")}
                </small>
              ) : dirty ? (
                <small>{t("inspector.files.unsaved", "Unsaved")}</small>
              ) : null}
              <button
                type="button"
                onClick={() => void workbench.saveFile()}
                disabled={
                  !trusted ||
                  hasActiveTurn ||
                  workbench.file.truncated ||
                  !dirty ||
                  workbench.loading
                }
              >
                {t("inspector.files.save", "Save")}
              </button>
            </div>
          </header>
          {downloadError && <p role="alert">{downloadError}</p>}
          <Suspense
            fallback={
              <div className={styles.editorLoading}>
                {t("inspector.files.loadingEditor", "Loading editor…")}
              </div>
            }
          >
            <LocalMonacoEditor
              height="100%"
              language={languageFor(workbench.file.path)}
              value={workbench.draft}
              onChange={(value) => workbench.setDraft(value ?? "")}
              theme={darkMode ? "vs-dark" : "vs-light"}
              options={{
                minimap: { enabled: false },
                readOnly:
                  !trusted || hasActiveTurn || workbench.file.truncated,
                fontSize: 12,
                lineHeight: 20,
                renderLineHighlight: "line",
                scrollBeyondLastLine: false,
                wordWrap: "on",
                automaticLayout: true,
              }}
            />
          </Suspense>
          {hasActiveTurn ? (
            <p className={styles.editorNotice}>
              {t(
                "inspector.files.locked",
                "Editing is locked until the active Turn finishes.",
              )}
            </p>
          ) : null}
        </div>
      ) : (
        <InspectorEmpty
          label={t("inspector.files.empty", "Select a text file to inspect it.")}
        />
      )}
    </div>
  );
}
