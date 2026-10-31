import { FolderOpen, Menu, MessageSquarePlus, RotateCw } from "lucide-react";
import { lazy, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";

import { projectCanExecute } from "./app/projectPresentation";
import { latestExecutingTurn } from "./app/interactiveTurnRouter";
import { useAppearance } from "./app/useAppearance";
import { useDesktopUi } from "./app/useDesktopUi";
import { useEscapeLayer } from "./app/escapeLayer";
import { FLOATING_REVIEW } from "./app/layout";
import { useFocusHandoff } from "./app/useFocusHandoff";
import { useMediaQuery } from "./app/useMediaQuery";
import { useNavigationDrawer } from "./app/useNavigationDrawer";
import { useComposerCommands } from "./app/useComposerCommands";
import { useWorkspaceController } from "./app/useWorkspaceController";
import { RuntimeNotice } from "./components/RuntimeNotice";
import {
  Composer,
  type ComposerLaunchIntent,
} from "./features/execution/Composer";
import { DesktopSidebar } from "./features/navigation/DesktopSidebar";
import { ConversationSplitter } from "./features/thread/ConversationSplitter";
import { ThreadHeader } from "./features/thread/ThreadHeader";
import { TrustFolderDialog } from "./features/thread/TrustFolderDialog";
import { useTranscriptMode } from "./features/thread/transcriptMode";
import type { ClientRuntime } from "./rpc/contracts";
import { initI18n } from "./app/i18n";
import styles from "./App.module.css";

// Idempotent: tests render <App> directly without main.tsx.
initI18n();

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
const SettingsDialog = lazy(() =>
  import("./features/settings/SettingsDialog").then((module) => ({
    default: module.SettingsDialog,
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

const NAVIGATION_ID = "app-navigation";
const REVIEW_PANEL_ID = "review-panel";
const REVIEW_TOGGLE_ID = "review-toggle";

export function App({ runtime }: { runtime: ClientRuntime }) {
  const { t } = useTranslation();
  const controller = useWorkspaceController(runtime);
  // Mounted for its effect: paints saved appearance preferences at startup.
  useAppearance();
  const ui = useDesktopUi();
  const {
    narrow,
    open: navigationOpen,
    buttonRef: navigationButtonRef,
    reveal: revealNavigation,
    close: closeNavigation,
  } = useNavigationDrawer(NAVIGATION_ID);
  const floatingReviewWidth = useMediaQuery(FLOATING_REVIEW);
  const transcript = useTranscriptMode();
  const runComposerCommand = useComposerCommands(controller, ui);
  const [composerIntent, setComposerIntent] =
    useState<ComposerLaunchIntent | null>(null);
  // The project whose "Trust folder" confirmation is open, if any.
  const [trustPromptFor, setTrustPromptFor] = useState<string | null>(null);
  const { state, selectedProject, selectedThread } = controller;
  const activeTurn = latestExecutingTurn(state.turns, selectedThread?.id);
  const queuedTurns = state.turns
    .filter(
      (turn) =>
        turn.threadId === selectedThread?.id && turn.status === "queued",
    )
    .sort((left, right) => left.ordinal - right.ordinal);
  const hasPendingTurn = state.turns.some(
    (turn) =>
      turn.threadId === selectedThread?.id &&
      ["queued", "running", "waiting_approval"].includes(turn.status),
  );
  const latestWorkflow =
    [...state.workflows].sort((left, right) =>
      right.updatedAt.localeCompare(left.updatedAt),
    )[0] ?? null;
  const activeWorkflow = latestWorkflow
    ? ["queued", "running", "waiting"].includes(latestWorkflow.status)
    : false;
  const workspaceAvailable = projectCanExecute(selectedProject);
  const threadUnreadable =
    selectedThread !== null && state.unreadableThreadId === selectedThread.id;
  const composerEditable =
    state.runtime.phase === "ready" &&
    workspaceAvailable &&
    selectedThread !== null &&
    !threadUnreadable;
  const agentExecutionEnabled =
    composerEditable && selectedProject?.trustState === "trusted";
  const disabledReason =
    state.runtime.phase !== "ready"
      ? t("app.disabled.waiting", "Waiting for the local App Server.")
      : !selectedProject
        ? t("app.disabled.noProject", "Open a project to begin.")
        : !workspaceAvailable
          ? t(
              "app.disabled.folderUnavailable",
              "The original folder is unavailable. This Session remains readable.",
            )
        : selectedProject.trustState !== "trusted"
          ? t("app.disabled.untrusted", "Trust this folder before agent execution.")
          : !selectedThread
            ? t("app.disabled.noThread", "Create a Session to begin.")
            : threadUnreadable
              ? t(
                  "app.disabled.unreadable",
                  "This Session's file can't be read right now.",
                )
              : null;
  const showingThreads = ui.destination === "threads";
  const inspectorVisible = Boolean(
    showingThreads && selectedThread && ui.inspectorOpen,
  );
  // On a narrower window the review panel floats over the page: it holds the
  // keyboard and Escape until it is closed, like a dialog. Its Escape layer
  // exists while the panel is open, so a dialog opened later stays above it
  // even if the window then narrows.
  const reviewFloating = inspectorVisible && floatingReviewWidth;
  useEscapeLayer(() => {
    if (reviewFloating) void ui.closeInspector();
  }, inspectorVisible);
  // A wider window stops it floating without closing it: focus stays put.
  useFocusHandoff(reviewFloating, REVIEW_PANEL_ID, () =>
    inspectorVisible ? null : document.getElementById(REVIEW_TOGGLE_ID),
  );

  return (
    <main
      className={styles.shell}
      data-inspector={inspectorVisible}
      data-layout={narrow ? "narrow" : undefined}
      data-review={reviewFloating ? "floating" : undefined}
    >
      <DesktopSidebar
        id={NAVIGATION_ID}
        drawer={narrow ? (navigationOpen ? "open" : "closed") : undefined}
        inert={reviewFloating}
        onRevealDrawer={revealNavigation}
        onCloseDrawer={closeNavigation}
        projects={state.projects}
        threads={state.threads}
        selectedProjectId={state.selectedProjectId}
        selectedThreadId={state.selectedThreadId}
        query={ui.sessionQuery}
        busy={state.busy}
        runtime={state.runtime}
        destination={ui.destination}
        settingsOpen={ui.settingsOpen}
        onDestination={(destination) => {
          closeNavigation();
          void ui.navigateTo(destination);
        }}
        onOpenSettings={() => {
          closeNavigation();
          ui.openSettings();
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
          closeNavigation();
          void (async () => {
            if (await ui.confirmDiscardInspectorDraft()) {
              await controller.createThread();
            }
          })();
        }}
        onSelectThread={(threadId) => {
          closeNavigation();
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
        onDeleteThread={async (threadId) => {
          if (
            threadId === state.selectedThreadId &&
            !(await ui.confirmDiscardInspectorDraft())
          ) {
            return;
          }
          await controller.deleteThread(threadId);
        }}
      />

      {navigationOpen ? (
        // A pointer target only: Escape and the menu button close it too.
        <div className={styles.navigationScrim} aria-hidden="true" onClick={closeNavigation} />
      ) : null}

      <section
        className={styles.workspace}
        aria-labelledby="thread-title"
        inert={navigationOpen || reviewFloating}
      >
        <button
          ref={navigationButtonRef}
          className={styles.navigationButton}
          type="button"
          aria-controls={NAVIGATION_ID}
          aria-expanded={navigationOpen}
          aria-label={t("sidebar.label", "Projects and Sessions")}
          onClick={revealNavigation}
        >
          <Menu size={18} />
        </button>
        <RuntimeNotice
          browserHost={runtime.host?.kind === "browser"}
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
              hasActiveWork={hasPendingTurn || activeWorkflow}
              onTrustProject={() => setTrustPromptFor(selectedProject?.id ?? null)}
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
              reviewButtonId={REVIEW_TOGGLE_ID}
            />

            <section className={styles.threadViewport}>
              {!selectedProject ? (
                <div className={styles.startState}>
                  <p className={styles.startEyebrow}>
                    {t("app.start.eyebrow", "Local coding agent")}
                  </p>
                  <h2>{t("app.start.title", "Open a folder to begin.")}</h2>
                  <p>
                    {t(
                      "app.start.body",
                      "Continue the same Sessions from DeepCode CLI, with tools, approvals, changes, and tests kept in one local workspace.",
                    )}
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
                      {t("app.start.openFolder", "Open project folder")}
                    </button>
                    <span>
                      {t(
                        "app.start.note",
                        "Local runtime · shared Session history",
                      )}
                    </span>
                  </div>
                </div>
              ) : !selectedThread ? (
                <div className={styles.startState}>
                  <p className={styles.startEyebrow}>
                    {selectedProject.displayName}
                  </p>
                  <h2>{t("app.noSessions.title", "No Sessions here yet.")}</h2>
                  <p>
                    {t(
                      "app.noSessions.body",
                      "Start with a clear outcome and a way to verify it. DeepCode will keep the conversation and execution trail together.",
                    )}
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
                      {t("app.noSessions.newThread", "New Session")}
                    </button>
                    <span>
                      {t("app.noSessions.shortcut", "⌘N from anywhere")}
                    </span>
                  </div>
                </div>
              ) : threadUnreadable ? (
                <div className={styles.startState} role="alert">
                  <p className={styles.startEyebrow}>{selectedThread.title}</p>
                  <h2>
                    {t(
                      "app.unreadable.title",
                      "This Session can't be read right now.",
                    )}
                  </h2>
                  <p>
                    {t(
                      "app.unreadable.body",
                      "Its file is on disk but may be locked, still syncing, or damaged. Nothing was deleted: once the file can be read, the conversation opens as before.",
                    )}
                  </p>
                  <div className={styles.startActions}>
                    <button
                      type="button"
                      disabled={state.busy}
                      onClick={() => void controller.selectThread(selectedThread.id)}
                    >
                      <RotateCw size={16} />
                      {t("app.unreadable.retry", "Try again")}
                    </button>
                  </div>
                </div>
              ) : (
                <Suspense
                  fallback={
                    <LoadingSurface>
                      {t("app.loading.session", "Loading Session…")}
                    </LoadingSurface>
                  }
                >
                  <ThreadConversation
                    key={selectedThread.id}
                    turns={state.turns}
                    items={state.items}
                    approvals={state.approvals}
                    plansByTurnId={state.plansByTurnId}
                    goalOutcome={state.goalOutcome}
                    historyLoaded={state.historyLoaded}
                    selectedItemId={state.selectedItemId}
                    transcriptMode={transcript.mode}
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
                    {t("app.loading.workflow", "Loading workflow controls…")}
                  </LoadingSurface>
                }
              >
                <WorkflowComposer
                  enabled={agentExecutionEnabled}
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
                editable={composerEditable}
                canExecute={agentExecutionEnabled}
                busy={state.busy}
                conversationStarted={state.turns.some(
                  (turn) => turn.threadId === selectedThread.id,
                )}
                executingTurn={activeTurn}
                queuedTurns={queuedTurns}
                runtime={runtime}
                project={selectedProject}
                thread={selectedThread}
                settings={state.settings}
                goal={state.goal}
                goalOutcome={state.goalOutcome}
                goalTurns={
                  state.goal
                    ? state.turns.filter(
                        (turn) => turn.goalId === state.goal?.id,
                      )
                    : []
                }
                disabledReason={disabledReason}
                transcriptMode={transcript.mode}
                onTranscriptModeChange={transcript.selectMode}
                onModelChange={(
                  connectionId,
                  model,
                  reasoningEffort,
                  contextWindow,
                ) =>
                  void controller.setThreadExecution(
                    connectionId,
                    model,
                    reasoningEffort,
                    contextWindow,
                  )
                }
                onAccessPresetChange={controller.setAccessPreset}
                onSetGoal={controller.setGoal}
                onPauseGoal={controller.pauseGoal}
                onResumeGoal={controller.resumeGoal}
                onContinueGoal={controller.continueGoal}
                onClearGoal={controller.clearGoal}
                onSelectGoalEvidence={(itemId) => {
                  controller.selectItem(itemId);
                  ui.openInspector("details");
                }}
                onPickContextFiles={controller.pickContextFiles}
                onCommand={runComposerCommand}
                onSend={controller.sendTurn}
                onQueue={controller.queueTurn}
                onInterrupt={() => void controller.interrupt()}
                launchIntent={composerIntent}
                onLaunchIntentConsumed={() => setComposerIntent(null)}
              />
            ) : null}
          </>
        ) : (
          <Suspense
            fallback={
              <LoadingSurface>
                {t("app.loading.workspace", "Loading workspace…")}
              </LoadingSurface>
            }
          >
            <ManagementWorkspace
              destination={ui.destination}
              runtime={runtime}
              project={selectedProject}
              onThreadCreated={controller.registerThread}
              onOpenThread={(threadId) => {
                void (async () => {
                  if (await ui.navigateTo("threads")) {
                    await controller.selectThread(threadId);
                  }
                })();
              }}
              onCreateSkill={async (skill) => {
                if (!(await ui.navigateTo("threads"))) return;
                const created = await controller.createThread(
                  "code",
                  "Create a Skill",
                );
                if (!created) return;
                setComposerIntent({
                  threadId: created.id,
                  prompt:
                    skill.defaultPrompt ??
                    `Use $${skill.name} to create a focused reusable Skill for this project.`,
                  skillIds: [skill.id],
                });
              }}
            />
          </Suspense>
        )}
        {showingThreads && selectedThread ? <ConversationSplitter /> : null}
      </section>

      {reviewFloating ? (
        <div
          className={styles.reviewScrim}
          aria-hidden="true"
          onClick={() => void ui.closeInspector()}
        />
      ) : null}
      {inspectorVisible ? (
        <section
          id={REVIEW_PANEL_ID}
          tabIndex={-1}
          className={styles.reviewPane}
          aria-label={t("app.reviewPanel", "Review panel")}
        >
          <Suspense
            fallback={
              <LoadingSurface>
                {t("app.loading.review", "Loading review…")}
              </LoadingSurface>
            }
          >
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

      {ui.settingsOpen ? (
        <Suspense fallback={null}>
          <SettingsDialog
            runtime={runtime}
            project={selectedProject}
            settings={state.settings}
            busy={state.busy}
            onRefresh={controller.refreshSettings}
            onUpdate={controller.updateSettings}
            onClose={ui.closeSettings}
            // Opened from the drawer, its button is hidden once it closes.
            returnFocus={narrow ? navigationButtonRef : undefined}
          />
        </Suspense>
      ) : null}

      {selectedProject &&
      trustPromptFor === selectedProject.id &&
      selectedProject.trustState === "untrusted" ? (
        <TrustFolderDialog
          runtime={runtime}
          project={selectedProject}
          thread={selectedThread}
          settings={state.settings}
          busy={state.busy}
          onCancel={() => setTrustPromptFor(null)}
          onConfirm={() => {
            setTrustPromptFor(null);
            void controller.trustProject();
          }}
        />
      ) : null}
    </main>
  );
}
