import { lazy, Suspense } from "react";

import { useSystemDarkMode } from "../../app/useSystemDarkMode";
import { confirmAction } from "../../platform/confirmAction";
import type { CodeWorkbenchController } from "../workbench/useCodeWorkbench";
import { InspectorEmpty } from "./InspectorEmpty";
import { languageFor } from "./inspectorFormat";
import styles from "./Inspector.module.css";

const LocalMonacoEditor = lazy(() => import("../workbench/LocalMonacoEditor"));

interface FilesPanelProps {
  trusted: boolean;
  hasActiveTurn: boolean;
  workbench: CodeWorkbenchController;
}

export function FilesPanel({
  trusted,
  hasActiveTurn,
  workbench,
}: FilesPanelProps) {
  const darkMode = useSystemDarkMode();
  const dirty = Boolean(
    workbench.file && workbench.draft !== workbench.file.content,
  );

  const openFile = async (path: string) => {
    if (
      dirty &&
      !(await confirmAction(
        "Discard the unsaved editor draft and open another file? Your edits cannot be recovered.",
        {
          confirmLabel: "Discard draft",
        },
      ))
    ) {
      return;
    }
    await workbench.openFile(path);
  };

  return (
    <div className={styles.filesView}>
      <div className={styles.fileTree} aria-label="Workspace files">
        {workbench.entries.map((entry) => (
          <button
            key={entry.path}
            type="button"
            disabled={entry.kind !== "file"}
            onClick={() => void openFile(entry.path)}
            style={{ paddingLeft: `${10 + (entry.path.split("/").length - 1) * 10}px` }}
          >
            <span aria-hidden="true">{entry.kind === "directory" ? "▾" : "·"}</span>
            {entry.name}
          </button>
        ))}
        {workbench.entriesTruncated ? (
          <p className={styles.treeNotice}>
            File list limited to the first 750 entries.
          </p>
        ) : null}
      </div>
      {workbench.file ? (
        <div className={styles.fileEditor}>
          <header>
            <span title={workbench.file.path}>{workbench.file.path}</span>
            <div>
              {workbench.file.truncated ? (
                <small>Truncated · read-only</small>
              ) : dirty ? (
                <small>Unsaved</small>
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
                Save
              </button>
            </div>
          </header>
          <Suspense fallback={<div className={styles.editorLoading}>Loading editor…</div>}>
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
              Editing is locked until the active Turn finishes.
            </p>
          ) : null}
        </div>
      ) : (
        <InspectorEmpty label="Select a text file to inspect it." />
      )}
    </div>
  );
}
