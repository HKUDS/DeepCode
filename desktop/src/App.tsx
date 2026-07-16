import { FolderOpen, MessageSquarePlus } from "lucide-react";
import { lazy, Suspense } from "react";

import { useDesktopUi } from "./app/useDesktopUi";
import { useComposerCommands } from "./app/useComposerCommands";
import { useWorkspaceController } from "./app/useWorkspaceController";
import { RuntimeNotice } from "./components/RuntimeNotice";
import { Composer } from "./features/execution/Composer";
import { DesktopSidebar } from "./features/navigation/DesktopSidebar";
import { ThreadHeader } from "./features/thread/ThreadHeader";
import type { DesktopRuntime } from "./rpc/contracts";
import { tauriRuntime } from "./rpc/tauriRuntime";
import styles from "./App.module.css";

const Inspector = lazy(() =>
  import("./features/inspector/Inspector").then((module) => ({
    default: module.Inspector,
  })),
);
const ManagementWorkspace = lazy(() =>
  import("./features/management/ManagementWorkspace").then((module) => ({
    default: module.ManagementWorkspace,
  })),
);
const ThreadConversation = lazy(() =>
  import("./features/thread/ThreadConversation").then((module) => ({
    default: module.ThreadConversation,
  })),
);
const WorkflowComposer = lazy(() =>
  import("./features/workflows/WorkflowComposer").then((module) => ({
    default: module.WorkflowComposer,
  })),
);

function LoadingSurface({
  children,
  compact = false,
}: {
  children: string;
  compact?: boolean;
}) {
  return (
    <div
      className={styles.loadingSurface}
      data-compact={compact || undefined}
      role="status"
    >
      {children}
    </div>
  );
}

export function App({ runtime = tauriRuntime }: { runtime?: DesktopRuntime }) {
  const controller = useWorkspaceController(runtime);
  const ui = useDesktopUi();
  const runComposerCommand = useComposerCommands(controller, ui);
  const { state, selectedProject, selectedThread } = controller;
  const activeTurn = state.turns.find((turn) =>
    ["queued", "running", "waiting_approval"].includes(turn.status),
  );
  const latestWorkflow =
    [...state.workflows].sort((left, right) =>
      right.updatedAt.localeCompare(left.updatedAt),
    )[0] ?? null;
  const activeWorkflow = latestWorkflow
    ? ["queued", "running", "waiting"].includes(latestWorkflow.status)
    : false;
  const composerEnabled =
    state.runtime.phase === "ready" &&
    selectedProject?.trustState === "trusted" &&
    selectedThread !== null;
  const disabledReason =
    state.runtime.phase !== "ready"
      ? "Waiting for the local App Server."
      : !selectedProject
        ? "Open a project to begin."
        : selectedProject.trustState !== "trusted"
          ? "Trust this folder before agent execution."
          : !selectedThread
            ? "Create a thread to begin."
            : null;
  const showingThreads = ui.destination === "threads";
  const inspectorVisible = Boolean(
    showingThreads && selectedThread && ui.inspectorOpen,
  );

  return (
    <main className={styles.shell} data-inspector={inspectorVisible}>
      <DesktopSidebar
        projects={state.projects}
        threads={state.threads}
        selectedProjectId={state.selectedProjectId}
        selectedThreadId={state.selectedThreadId}
        query={ui.sessionQuery}
        busy={state.busy}
        runtime={state.runtime}
        destination={ui.destination}
        onDestination={(destination) => {
          void ui.navigateTo(destination);
        }}
        onQueryChange={ui.setSessionQuery}
        onOpenProject={() => {
          void (async () => {
            if (await ui.confirmDiscardInspectorDraft()) {
              await controller.openProject();
            }
          })();
        }}
        onSelectProject={(projectId) => {
          if (projectId === state.selectedProjectId) return;
          void (async () => {
            if (await ui.confirmDiscardInspectorDraft()) {
              await controller.selectProject(projectId);
            }
          })();
        }}
        onCreateThread={() => {
          void (async () => {
            if (await ui.confirmDiscardInspectorDraft()) {
              await controller.createThread();
            }
          })();
        }}
        onSelectThread={(threadId) => {
          if (threadId === state.selectedThreadId) return;
          void (async () => {
            if (await ui.confirmDiscardInspectorDraft()) {
              await controller.selectThread(threadId);
            }
          })();
        }}
        onRenameThread={controller.renameThread}
        onArchiveThread={async (threadId) => {
          if (
            threadId === state.selectedThreadId &&
            !(await ui.confirmDiscardInspectorDraft())
          ) {
            return;
          }
          await controller.archiveThread(threadId);
        }}
      />

      <section className={styles.workspace} aria-labelledby="thread-title">
        <RuntimeNotice
          runtime={state.runtime}
          error={state.error}
          busy={state.busy}
          onRestart={() => void controller.restartRuntime()}
          onDismissError={controller.dismissError}
        />
        {showingThreads ? (
          <>
            <ThreadHeader
              project={selectedProject}
              thread={selectedThread}
              runtime={state.runtime}
              busy={state.busy}
              inspectorOpen={inspectorVisible}
              hasActiveWork={Boolean(activeTurn) || activeWorkflow}
              onTrustProject={() => void controller.trustProject()}
              onForkThread={() => {
                void (async () => {
                  if (await ui.confirmDiscardInspectorDraft()) {
                    await controller.forkThread();
                  }
                })();
              }}
              onCreatePaperThread={() => {
                void (async () => {
                  if (await ui.confirmDiscardInspectorDraft()) {
                    await controller.createThread("paper");
                  }
                })();
              }}
              onToggleInspector={() => void ui.toggleInspector()}
            />

            <section className={styles.threadViewport}>
              {!selectedProject ? (
                <div className={styles.startState}>
                  <div className={styles.startSignal} aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </div>
                  <p className={styles.startEyebrow}>Local coding workspace</p>
                  <h2>
                    Open a project.
                    <br />
                    Pick up any Session.
                  </h2>
                  <p>
                    DeepCode Desktop reads the same local history as the CLI. Choose
                    a folder once, then continue an existing Session or start a new
                    task without importing or duplicating anything.
                  </p>
                  <div className={styles.startActions}>
                    <button
                      type="button"
                      onClick={() => {
                        void (async () => {
                          if (await ui.confirmDiscardInspectorDraft()) {
                            await controller.openProject();
                          }
                        })();
                      }}
                    >
                      <FolderOpen size={16} />
                      Open project folder
                    </button>
                    <span>Runs locally · no web server</span>
                  </div>
                  <div className={styles.startTrace} aria-label="Workspace guarantees">
                    <span>
                      <i data-tone="signal" />
                      Shared CLI history
                    </span>
                    <span>
                      <i data-tone="success" />
                      Project-scoped tools
                    </span>
                    <span>
                      <i />
                      Recoverable Sessions
                    </span>
                  </div>
                </div>
              ) : !selectedThread ? (
                <div className={styles.startState}>
                  <div className={styles.startSignal} aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </div>
                  <p className={styles.startEyebrow}>{selectedProject.displayName}</p>
                  <h2>Start the first thread in {selectedProject.displayName}.</h2>
                  <p>
                    Give the task a clear outcome and verification target. The
                    thread keeps its own Agent context while remaining part of the
                    same canonical Session history.
                  </p>
                  <div className={styles.startActions}>
                    <button
                      type="button"
                      onClick={() => {
                        void (async () => {
                          if (await ui.confirmDiscardInspectorDraft()) {
                            await controller.createThread();
                          }
                        })();
                      }}
                    >
                      <MessageSquarePlus size={16} />
                      New thread
                    </button>
                    <span>⌘N from anywhere</span>
                  </div>
                  <div className={styles.startTrace} aria-label="Thread guarantees">
                    <span>
                      <i data-tone="signal" />
                      One Agent context
                    </span>
                    <span>
                      <i data-tone="success" />
                      Durable review trail
                    </span>
                    <span>
                      <i />
                      Safe restart recovery
                    </span>
                  </div>
                </div>
              ) : (
                <Suspense
                  fallback={<LoadingSurface>Loading Session…</LoadingSurface>}
                >
                  <ThreadConversation
                    turns={state.turns}
                    items={state.items}
                    approvals={state.approvals}
                    selectedItemId={state.selectedItemId}
                    busy={state.busy}
                    onSelectItem={controller.selectItem}
                    onOpenInspector={ui.openInspector}
                    onRespondToApproval={(approvalId, decision) =>
                      void controller.respondToApproval(approvalId, decision)
                    }
                    onRetryTurn={(turnId) => void controller.retryTurn(turnId)}
                    onCancelQueuedTurn={(turnId) =>
                      void controller.interruptTurn(turnId)
                    }
                  />
                </Suspense>
              )}
            </section>

            {selectedThread?.mode === "paper" ? (
              <Suspense
                fallback={
                  <LoadingSurface compact>
                    Loading workflow controls…
                  </LoadingSurface>
                }
              >
                <WorkflowComposer
                  enabled={composerEnabled}
                  busy={state.busy}
                  workflow={latestWorkflow}
                  disabledReason={disabledReason}
                  onPickFile={controller.pickWorkflowFile}
                  onStart={controller.startWorkflow}
                  onRetry={controller.retryWorkflow}
                  onRespond={controller.respondToWorkflow}
                  onInterrupt={() => void controller.interrupt()}
                />
              </Suspense>
            ) : selectedThread ? (
              <Composer
                key={selectedThread.id}
                enabled={composerEnabled}
                busy={state.busy}
                active={Boolean(activeTurn)}
                project={selectedProject}
                thread={selectedThread}
                settings={state.settings}
                disabledReason={disabledReason}
                onModelChange={(model) => void controller.setThreadModel(model)}
                onPermissionModeChange={(mode) =>
                  void controller.setPermissionMode(mode)
                }
                onPickContextFiles={controller.pickContextFiles}
                onCommand={runComposerCommand}
                onSubmit={controller.startTurn}
                onQueue={controller.queueTurn}
                onInterrupt={() => void controller.interrupt()}
              />
            ) : null}
          </>
        ) : (
          <Suspense
            fallback={<LoadingSurface>Loading workspace…</LoadingSurface>}
          >
            <ManagementWorkspace
              destination={ui.destination}
              runtime={runtime}
              project={selectedProject}
              settings={state.settings}
              busy={state.busy}
              onRefreshSettings={controller.refreshSettings}
              onUpdateSettings={controller.updateSettings}
              onThreadCreated={controller.registerThread}
              onOpenThread={(threadId) => {
                void (async () => {
                  if (await ui.navigateTo("threads")) {
                    await controller.selectThread(threadId);
                  }
                })();
              }}
            />
          </Suspense>
        )}
      </section>

      {inspectorVisible ? (
        <section className={styles.reviewPane} aria-label="Review panel">
          <Suspense fallback={<LoadingSurface>Loading review…</LoadingSurface>}>
            <Inspector
              runtime={runtime}
              thread={selectedThread}
              trusted={selectedProject?.trustState === "trusted"}
              turns={state.turns}
              items={state.items}
              workflows={state.workflows}
              artifacts={state.artifacts}
              selectedItemId={state.selectedItemId}
              tab={ui.inspectorTab}
              onSelectItem={controller.selectItem}
              onTabChange={ui.setInspectorTab}
              onDirtyChange={ui.setInspectorDirty}
              onClose={() => void ui.closeInspector()}
            />
          </Suspense>
        </section>
      ) : null}
    </main>
  );
}
