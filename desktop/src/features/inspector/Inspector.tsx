import { useEffect, useMemo } from "react";
import { X } from "lucide-react";

import type {
  Artifact,
  Item,
  Thread,
  Turn,
  WorkflowRun,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import type { DesktopInspectorTab } from "../../app/useDesktopUi";
import { TerminalPanel } from "../workbench/TerminalPanel";
import { useCodeWorkbench } from "../workbench/useCodeWorkbench";
import { ArtifactsPanel } from "./ArtifactsPanel";
import { ChangesPanel } from "./ChangesPanel";
import { DetailsPanel } from "./DetailsPanel";
import { FilesPanel } from "./FilesPanel";
import { TestsPanel } from "./TestsPanel";
import styles from "./Inspector.module.css";

interface InspectorProps {
  runtime: DesktopRuntime;
  thread: Thread | null;
  trusted: boolean;
  turns: Turn[];
  items: Item[];
  workflows: WorkflowRun[];
  artifacts: Artifact[];
  selectedItemId: string | null;
  tab: DesktopInspectorTab;
  onSelectItem(itemId: string): void;
  onTabChange(tab: DesktopInspectorTab): void;
  onDirtyChange(dirty: boolean): void;
  onClose(): void;
}

const tabs: DesktopInspectorTab[] = [
  "changes",
  "files",
  "artifacts",
  "tests",
  "terminal",
  "details",
];

export function Inspector({
  runtime,
  thread,
  trusted,
  turns,
  items,
  workflows,
  artifacts,
  selectedItemId,
  tab,
  onSelectItem,
  onTabChange,
  onDirtyChange,
  onClose,
}: InspectorProps) {
  const workbench = useCodeWorkbench(runtime, thread);
  const selected = items.find((item) => item.id === selectedItemId) ?? null;
  const dirty = Boolean(
    workbench.file && workbench.draft !== workbench.file.content,
  );
  const latestTurn = useMemo(
    () =>
      [...turns]
        .sort((left, right) => right.ordinal - left.ordinal)
        .find((turn) =>
          ["completed", "failed", "interrupted"].includes(turn.status),
        ) ?? null,
    [turns],
  );
  const latestWorkflow = useMemo(
    () =>
      [...workflows].sort((left, right) =>
        right.updatedAt.localeCompare(left.updatedAt),
      )[0] ?? null,
    [workflows],
  );
  const hasActiveTurn = turns.some((turn) =>
    ["queued", "running", "waiting_approval"].includes(turn.status),
  );

  useEffect(() => {
    if (!dirty) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [dirty]);

  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);

  const openFile = (path: string) => {
    onTabChange("files");
    void workbench.openFile(path);
  };

  const badge = (candidate: DesktopInspectorTab): number | null => {
    if (candidate === "changes") return workbench.diffs.length || null;
    if (candidate === "artifacts") return artifacts.length || null;
    return null;
  };

  return (
    <aside className={styles.inspector} aria-label="Inspector">
      <div className={styles.tabs} role="tablist" aria-label="Inspector views">
        {tabs.map((candidate) => (
          <button
            key={candidate}
            type="button"
            role="tab"
            aria-selected={tab === candidate}
            onClick={() => onTabChange(candidate)}
          >
            {candidate}
            {badge(candidate) ? ` ${badge(candidate)}` : ""}
          </button>
        ))}
      </div>
      <button
        className={styles.close}
        type="button"
        onClick={onClose}
        aria-label="Close review panel"
      >
        <X size={15} />
      </button>

      <div className={styles.panel}>
        {workbench.error ? <p className={styles.error}>{workbench.error}</p> : null}
        {tab === "changes" ? (
          <ChangesPanel
            thread={thread}
            trusted={trusted}
            hasActiveTurn={hasActiveTurn}
            workbench={workbench}
            onOpenFile={openFile}
          />
        ) : null}
        {tab === "files" ? (
          <FilesPanel
            trusted={trusted}
            hasActiveTurn={hasActiveTurn}
            workbench={workbench}
          />
        ) : null}
        {tab === "artifacts" ? (
          <ArtifactsPanel
            key={thread?.id ?? "no-thread"}
            runtime={runtime}
            workflow={latestWorkflow}
            artifacts={artifacts}
          />
        ) : null}
        {tab === "tests" ? (
          <TestsPanel
            trusted={trusted}
            hasActiveTurn={hasActiveTurn}
            latestTurn={latestTurn}
            workbench={workbench}
          />
        ) : null}
        <TerminalPanel
          key={thread?.id ?? "no-thread"}
          runtime={runtime}
          threadId={thread?.id ?? null}
          enabled={trusted}
          active={tab === "terminal"}
        />
        {tab === "details" ? (
          <DetailsPanel
            selected={selected}
            items={items}
            onSelectItem={onSelectItem}
          />
        ) : null}
      </div>
    </aside>
  );
}
