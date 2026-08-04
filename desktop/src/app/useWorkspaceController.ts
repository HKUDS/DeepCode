import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

import type {
  ApprovalDecision,
  ConfigScope,
  Event,
  ExecutionAccessPreset,
  GoalSetParams,
  JsonObject,
  Project,
  Thread,
  ThreadMode,
  TurnStartParams,
  WorkflowStartParams,
} from "../generated/app-server";
import { confirmAction } from "../platform/confirmAction";
import type {
  AnyRpcNotification,
  BridgeError,
  DesktopRuntime,
  SidecarStatus,
} from "../rpc/contracts";
import {
  latestExecutingTurn,
  sendInteractiveTurn,
  type InteractiveDelivery,
} from "./interactiveTurnRouter";
import { replayThreadHistory } from "./replayThreadHistory";
import {
  initialWorkspaceState,
  workspaceReducer,
  type WorkspaceState,
} from "./workspaceState";

const PROJECT_KEY = "deepcode.desktop.selectedProject";
const THREAD_KEY = "deepcode.desktop.selectedThread";

function normalizeError(error: unknown): BridgeError {
  if (typeof error === "object" && error !== null && "message" in error) {
    const candidate = error as Partial<BridgeError>;
    return {
      code: candidate.code ?? "DESKTOP_ERROR",
      message: String(candidate.message),
      retryable: candidate.retryable === true,
      data: candidate.data,
    };
  }
  return {
    code: "DESKTOP_ERROR",
    message: error instanceof Error ? error.message : String(error),
    retryable: false,
  };
}

function isEvent(value: unknown): value is Event {
  return (
    typeof value === "object" &&
    value !== null &&
    "sequence" in value &&
    typeof (value as { sequence?: unknown }).sequence === "number"
  );
}

function titleFromPrompt(prompt: string): string {
  return (prompt.trim().split(/\r?\n/, 1)[0] ?? "").trim().slice(0, 60);
}

export interface GoalDefinitionInput {
  objective: string;
  tokenBudget: number | null;
  skillIds: string[];
  resume?: boolean;
}

export interface WorkspaceController {
  state: WorkspaceState;
  selectedProject: Project | null;
  selectedThread: Thread | null;
  openProject(): Promise<void>;
  selectProject(projectId: string): Promise<void>;
  trustProject(): Promise<void>;
  createThread(mode?: ThreadMode, title?: string): Promise<Thread | undefined>;
  forkThread(): Promise<void>;
  selectThread(threadId: string): Promise<void>;
  renameThread(threadId: string, title: string): Promise<void>;
  archiveThread(threadId: string): Promise<void>;
  deleteThread(threadId: string): Promise<void>;
  registerThread(thread: Thread): void;
  setThreadModel(model: string | null): Promise<void>;
  setThreadExecution(
    connectionId: string | null,
    model: string | null,
    reasoningEffort: string | null,
  ): Promise<void>;
  setAccessPreset(preset: ExecutionAccessPreset | null): Promise<boolean>;
  refreshSettings(): Promise<void>;
  updateSettings(
    patch: JsonObject,
    scope?: ConfigScope,
    riskAcknowledged?: boolean,
  ): Promise<void>;
  setGoal(input: GoalDefinitionInput): Promise<void>;
  pauseGoal(): Promise<void>;
  resumeGoal(): Promise<void>;
  continueGoal(): Promise<void>;
  clearGoal(): Promise<void>;
  sendTurn(
    prompt: string,
    skillIds?: string[],
  ): Promise<InteractiveDelivery | null>;
  queueTurn(prompt: string, skillIds?: string[]): Promise<boolean>;
  retryTurn(turnId: string): Promise<void>;
  interruptTurn(turnId: string): Promise<void>;
  pickContextFiles(): Promise<string[]>;
  pickWorkflowFile(): Promise<string | null>;
  startWorkflow(
    sourceType: WorkflowStartParams["sourceType"],
    source: string,
    options: NonNullable<WorkflowStartParams["options"]>,
  ): Promise<void>;
  retryWorkflow(workflowRunId: string): Promise<void>;
  respondToWorkflow(
    workflowRunId: string,
    interactionId: string,
    action: "approve" | "modify" | "cancel",
    feedback?: string,
  ): Promise<void>;
  interrupt(): Promise<void>;
  respondToApproval(approvalId: string, decision: ApprovalDecision): Promise<void>;
  selectItem(itemId: string | null): void;
  restartRuntime(): Promise<void>;
  dismissError(): void;
}

export function useWorkspaceController(runtime: DesktopRuntime): WorkspaceController {
  const [state, dispatch] = useReducer(workspaceReducer, initialWorkspaceState);
  const selectedThreadRef = useRef<string | null>(null);
  const loadedRuntimeRef = useRef(false);

  const reportError = useCallback((error: unknown) => {
    dispatch({ type: "error", error: normalizeError(error) });
  }, []);

  const replayThread = useCallback(
    async (threadId: string) => {
      dispatch({ type: "trace-reset" });
      await replayThreadHistory(runtime, threadId, (event) => {
          dispatch({ type: "event", event });
      });
    },
    [runtime],
  );

  const loadSettings = useCallback(
    async (projectId?: string | null) => {
      const result = await runtime.request("settings/read", {
        ...(projectId ? { projectId } : {}),
      });
      dispatch({ type: "settings", settings: result.settings });
    },
    [runtime],
  );

  const loadGoal = useCallback(
    async (threadId: string) => {
      const result = await runtime.request("thread/goal/get", { threadId });
      dispatch({
        type: "goal",
        goal: result.goal,
        outcome: result.outcome,
      });
    },
    [runtime],
  );

  const loadThreads = useCallback(
    async (
      projectId: string,
      preferredThreadId?: string | null,
      allowCrossProjectPreferred = false,
    ) => {
      const result = await runtime.request("thread/list", {
        includeArchived: false,
        limit: 500,
      });
      const preferred = result.threads.find(
        (thread) =>
          thread.id === preferredThreadId &&
          (allowCrossProjectPreferred || thread.projectId === projectId),
      );
      const selectedThread =
        preferred ?? result.threads.find((thread) => thread.projectId === projectId) ?? null;
      const selected = selectedThread?.id ?? null;
      dispatch({ type: "threads", threads: result.threads, selectedThreadId: selected });
      if (selectedThread && selectedThread.projectId !== projectId) {
        dispatch({ type: "select-project", projectId: selectedThread.projectId });
        dispatch({ type: "select-thread", threadId: selectedThread.id });
        localStorage.setItem(PROJECT_KEY, selectedThread.projectId);
      }
      selectedThreadRef.current = selected;
      if (selected) {
        localStorage.setItem(THREAD_KEY, selected);
        const resumed = await runtime.request("thread/resume", {
          sessionId: selected,
        });
        dispatch({ type: "thread-upsert", thread: resumed.thread });
        await replayThread(selected);
        await loadGoal(selected);
        return resumed.thread;
      }
      return null;
    },
    [loadGoal, replayThread, runtime],
  );

  const loadProjects = useCallback(async () => {
    if (loadedRuntimeRef.current) {
      return;
    }
    loadedRuntimeRef.current = true;
    try {
      const result = await runtime.request("project/list", { limit: 500 });
      const preferredProject = localStorage.getItem(PROJECT_KEY);
      const selected =
        result.projects.find((project) => project.id === preferredProject)?.id ??
        result.projects[0]?.id ??
        null;
      dispatch({ type: "projects", projects: result.projects, selectedProjectId: selected });
      if (selected) {
        localStorage.setItem(PROJECT_KEY, selected);
        const selectedThread = await loadThreads(
          selected,
          localStorage.getItem(THREAD_KEY),
          true,
        );
        await loadSettings(selectedThread?.projectId ?? selected);
      } else {
        await loadSettings();
      }
    } catch (error) {
      loadedRuntimeRef.current = false;
      reportError(error);
    }
  }, [loadSettings, loadThreads, reportError, runtime]);

  useEffect(() => {
    let disposed = false;
    const cleanups: Array<() => void> = [];

    const acceptStatus = (status: SidecarStatus) => {
      if (disposed) return;
      dispatch({ type: "runtime", status });
      if (status.phase === "ready") {
        void loadProjects();
      } else if (status.phase === "crashed" || status.phase === "stopped") {
        loadedRuntimeRef.current = false;
      }
    };

    const acceptNotification = (notification: AnyRpcNotification) => {
      if (disposed) return;
      if (notification.method === "server.warning") {
        const threadId = selectedThreadRef.current;
        if (threadId) void replayThread(threadId).catch(reportError);
        return;
      }
      if (isEvent(notification.params)) {
        if (notification.method === "thread.updated") {
          dispatch({ type: "event", event: notification.params });
          return;
        }
        const threadId = selectedThreadRef.current;
        if (threadId === notification.params.threadId) {
          dispatch({ type: "event", event: notification.params });
        }
      }
    };

    void (async () => {
      try {
        cleanups.push(await runtime.onStatus(acceptStatus));
        cleanups.push(await runtime.onNotification(acceptNotification));
        cleanups.push(
          await runtime.onLog((message) => dispatch({ type: "log", message })),
        );
        acceptStatus(await runtime.status());
      } catch (error) {
        reportError(error);
      }
    })();

    return () => {
      disposed = true;
      for (const cleanup of cleanups) cleanup();
    };
  }, [loadProjects, replayThread, reportError, runtime]);

  const withBusy = useCallback(
    async <Result,>(
      operation: () => Promise<Result>,
    ): Promise<Result | undefined> => {
      dispatch({ type: "busy", busy: true });
      dispatch({ type: "error", error: null });
      try {
        return await operation();
      } catch (error) {
        reportError(error);
        return undefined;
      } finally {
        dispatch({ type: "busy", busy: false });
      }
    },
    [reportError],
  );

  const openProject = useCallback(
    () =>
      withBusy(async () => {
        const path = await runtime.pickDirectory();
        if (!path) return;
        const result = await runtime.request("project/add", {
          path,
          trustState: "untrusted",
        });
        dispatch({ type: "project-upsert", project: result.project });
        dispatch({ type: "select-project", projectId: result.project.id });
        selectedThreadRef.current = null;
        localStorage.setItem(PROJECT_KEY, result.project.id);
        await loadThreads(result.project.id);
        await loadSettings(result.project.id);
      }),
    [loadSettings, loadThreads, runtime, withBusy],
  );

  const selectProject = useCallback(
    (projectId: string) =>
      withBusy(async () => {
        dispatch({ type: "select-project", projectId });
        selectedThreadRef.current = null;
        localStorage.setItem(PROJECT_KEY, projectId);
        await loadThreads(projectId, localStorage.getItem(THREAD_KEY));
        await loadSettings(projectId);
      }),
    [loadSettings, loadThreads, withBusy],
  );

  const selectedProject =
    state.projects.find((project) => project.id === state.selectedProjectId) ?? null;
  const selectedThread =
    state.threads.find((thread) => thread.id === state.selectedThreadId) ?? null;

  const trustProject = useCallback(
    () =>
      withBusy(async () => {
        if (!selectedProject) return;
        const result = await runtime.request("project/update", {
          projectId: selectedProject.id,
          trustState: "trusted",
        });
        dispatch({ type: "project-upsert", project: result.project });
      }),
    [runtime, selectedProject, withBusy],
  );

  const createThread = useCallback(
    (mode: ThreadMode = "code", title?: string) =>
      withBusy(async () => {
        if (!selectedProject) return;
        const result = await runtime.request("thread/start", {
          projectId: selectedProject.id,
          title: title ?? (mode === "paper" ? "New Paper2Code run" : "New task"),
          mode,
        });
        dispatch({ type: "thread-upsert", thread: result.thread });
        dispatch({ type: "select-thread", threadId: result.thread.id });
        selectedThreadRef.current = result.thread.id;
        localStorage.setItem(THREAD_KEY, result.thread.id);
        return result.thread;
      }),
    [runtime, selectedProject, withBusy],
  );

  const forkThread = useCallback(
    () =>
      withBusy(async () => {
        if (!selectedThread) return;
        const forked = await runtime.request("thread/fork", {
          threadId: selectedThread.id,
          title: `Fork of ${selectedThread.title}`,
        });
        const isolated = await runtime.request("git/worktree/create", {
          threadId: forked.thread.id,
        });
        dispatch({ type: "thread-upsert", thread: isolated.thread });
        dispatch({ type: "select-thread", threadId: isolated.thread.id });
        selectedThreadRef.current = isolated.thread.id;
        localStorage.setItem(THREAD_KEY, isolated.thread.id);
      }),
    [runtime, selectedThread, withBusy],
  );

  const activateThread = useCallback(
    async (target: Thread) => {
      if (target.projectId !== state.selectedProjectId) {
        dispatch({ type: "select-project", projectId: target.projectId });
        localStorage.setItem(PROJECT_KEY, target.projectId);
      }
      const resumed = await runtime.request("thread/resume", {
        sessionId: target.id,
      });
      dispatch({ type: "thread-upsert", thread: resumed.thread });
      dispatch({ type: "select-thread", threadId: target.id });
      selectedThreadRef.current = target.id;
      localStorage.setItem(THREAD_KEY, target.id);
      await replayThread(target.id);
      await loadGoal(target.id);
      await loadSettings(target.projectId);
    },
    [loadGoal, loadSettings, replayThread, runtime, state.selectedProjectId],
  );

  const selectThread = useCallback(
    (threadId: string) =>
      withBusy(async () => {
        const target = state.threads.find((thread) => thread.id === threadId);
        if (!target) return;
        await activateThread(target);
      }),
    [activateThread, state.threads, withBusy],
  );

  const renameThread = useCallback(
    (threadId: string, title: string) =>
      withBusy(async () => {
        const cleanTitle = title.trim();
        if (!cleanTitle) return;
        const result = await runtime.request("thread/rename", {
          threadId,
          title: cleanTitle,
        });
        dispatch({ type: "thread-upsert", thread: result.thread });
      }),
    [runtime, withBusy],
  );

  const removeThreadLocally = useCallback(
    async (target: Thread) => {
      const wasSelected = selectedThreadRef.current === target.id;
      const remaining = state.threads.filter((thread) => thread.id !== target.id);
      dispatch({ type: "thread-remove", threadId: target.id });
      if (!wasSelected) return;

      selectedThreadRef.current = null;
      localStorage.removeItem(THREAD_KEY);
      const replacement =
        remaining
          .filter((thread) => thread.projectId === target.projectId)
          .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0] ??
        null;
      if (replacement) await activateThread(replacement);
    },
    [activateThread, state.threads],
  );

  const archiveThread = useCallback(
    (threadId: string) =>
      withBusy(async () => {
        const target = state.threads.find((thread) => thread.id === threadId);
        if (!target) return;
        await runtime.request("thread/archive", { threadId });
        await removeThreadLocally(target);
      }),
    [removeThreadLocally, runtime, state.threads, withBusy],
  );

  const deleteThread = useCallback(
    (threadId: string) =>
      withBusy(async () => {
        const target = state.threads.find((thread) => thread.id === threadId);
        if (!target) return;
        await runtime.request("thread/delete", { threadId });
        await removeThreadLocally(target);
      }),
    [removeThreadLocally, runtime, state.threads, withBusy],
  );

  const registerThread = useCallback((thread: Thread) => {
    dispatch({ type: "thread-upsert", thread });
  }, []);

  const setThreadModel = useCallback(
    (model: string | null) =>
      withBusy(async () => {
        if (!selectedThread) return;
        const result = await runtime.request("thread/model", {
          threadId: selectedThread.id,
          model,
        });
        dispatch({ type: "thread-upsert", thread: result.thread });
      }),
    [runtime, selectedThread, withBusy],
  );

  const setThreadExecution = useCallback(
    (
      connectionId: string | null,
      model: string | null,
      reasoningEffort: string | null,
    ) =>
      withBusy(async () => {
        if (!selectedThread) return;
        const result = await runtime.request("thread/execution/update", {
          threadId: selectedThread.id,
          connectionId,
          model,
          reasoningEffort,
        });
        dispatch({ type: "thread-upsert", thread: result.thread });
      }),
    [runtime, selectedThread, withBusy],
  );

  const applySettings = useCallback(
    async (
      patch: JsonObject,
      scope: ConfigScope = "user",
      riskAcknowledged = false,
    ) => {
      const result = await runtime.request("settings/update", {
        patch,
        scope,
        ...(selectedProject ? { projectId: selectedProject.id } : {}),
        ...(riskAcknowledged ? { riskAcknowledged: true } : {}),
      });
      dispatch({ type: "settings", settings: result.settings });
    },
    [runtime, selectedProject],
  );

  const setAccessPreset = useCallback(
    async (preset: ExecutionAccessPreset | null): Promise<boolean> => {
      if (!selectedThread) return false;
      if (selectedThread.accessPresetOverride === preset) return true;
      if (
        preset === "full_access" &&
        !(await confirmAction(
          "Full access lets tools run without approval and outside the workspace " +
            "sandbox for this Session. DeepCode may read, modify, or execute " +
            "files anywhere your account can access.",
          {
            title: "Enable Full access?",
            kind: "warning",
            confirmLabel: "Enable Full access",
            cancelLabel: "Keep current access",
          },
        ))
      ) {
        return false;
      }
      const applied = await withBusy(async () => {
        const result = await runtime.request("thread/permission/update", {
          threadId: selectedThread.id,
          accessPreset: preset,
          ...(preset === "full_access" ? { riskAcknowledged: true } : {}),
        });
        dispatch({ type: "thread-upsert", thread: result.thread });
        return true;
      });
      return applied === true;
    },
    [runtime, selectedThread, withBusy],
  );

  const refreshSettings = useCallback(
    () => withBusy(() => loadSettings(selectedProject?.id)),
    [loadSettings, selectedProject?.id, withBusy],
  );

  const updateSettings = useCallback(
    (
      patch: JsonObject,
      scope: ConfigScope = "user",
      riskAcknowledged = false,
    ) => withBusy(() => applySettings(patch, scope, riskAcknowledged)),
    [applySettings, withBusy],
  );

  const setGoal = useCallback(
    (input: GoalDefinitionInput) =>
      withBusy(async () => {
        if (!selectedThread) return;
        const current = state.goal;
        let result = await runtime.request("thread/goal/set", {
          threadId: selectedThread.id,
          objective: input.objective,
          tokenBudget: input.tokenBudget,
          skills: input.skillIds as GoalSetParams["skills"],
          ...(current ? { expectedGoalId: current.id } : {}),
          start: true,
        });
        if (input.resume && result.goal && result.goal.status !== "active") {
          result = await runtime.request("thread/goal/resume", {
            threadId: selectedThread.id,
            expectedGoalId: result.goal.id,
          });
        }
        dispatch({
          type: "goal",
          goal: result.goal,
          outcome: result.outcome,
        });
      }),
    [runtime, selectedThread, state.goal, withBusy],
  );

  const pauseGoal = useCallback(
    () =>
      withBusy(async () => {
        if (!selectedThread || !state.goal) return;
        const result = await runtime.request("thread/goal/pause", {
          threadId: selectedThread.id,
          expectedGoalId: state.goal.id,
        });
        dispatch({
          type: "goal",
          goal: result.goal,
          outcome: result.outcome,
        });
      }),
    [runtime, selectedThread, state.goal, withBusy],
  );

  const resumeGoal = useCallback(
    () =>
      withBusy(async () => {
        if (!selectedThread || !state.goal) return;
        const result = await runtime.request("thread/goal/resume", {
          threadId: selectedThread.id,
          expectedGoalId: state.goal.id,
        });
        dispatch({
          type: "goal",
          goal: result.goal,
          outcome: result.outcome,
        });
      }),
    [runtime, selectedThread, state.goal, withBusy],
  );

  const continueGoal = useCallback(
    () =>
      withBusy(async () => {
        if (!selectedThread || !state.goal) return;
        const result = await runtime.request("thread/goal/continue", {
          threadId: selectedThread.id,
          expectedGoalId: state.goal.id,
        });
        dispatch({
          type: "goal",
          goal: result.goal,
          outcome: result.outcome,
        });
      }),
    [runtime, selectedThread, state.goal, withBusy],
  );

  const clearGoal = useCallback(
    () =>
      withBusy(async () => {
        if (!selectedThread || !state.goal) return;
        const result = await runtime.request("thread/goal/clear", {
          threadId: selectedThread.id,
          expectedGoalId: state.goal.id,
        });
        dispatch({
          type: "goal",
          goal: result.goal,
          outcome: result.outcome,
        });
      }),
    [runtime, selectedThread, state.goal, withBusy],
  );

  const sendTurn = useCallback(
    async (
      prompt: string,
      skillIds: string[] = [],
    ): Promise<InteractiveDelivery | null> => {
      if (!selectedThread) return null;
      const shouldTitleSession =
        selectedThread.title === "New task" && state.turns.length === 0;
      const active = latestExecutingTurn(state.turns, selectedThread.id);
      const result = await withBusy(() =>
        sendInteractiveTurn(runtime, {
          threadId: selectedThread.id,
          prompt,
          cachedActiveTurnId: active?.id ?? null,
          skillIds,
        }),
      );
      if (!result) return null;
      if (result.delivery === "started" || result.delivery === "queued") {
        dispatch({ type: "snapshot", snapshot: result.snapshot });
      } else {
        dispatch({
          type: "snapshot",
          snapshot: { turn: result.turn, items: [], approvals: [] },
        });
      }
      if (
        (result.delivery === "started" || result.delivery === "queued") &&
        shouldTitleSession
      ) {
        const title = titleFromPrompt(prompt);
        if (title) {
          try {
            const renamed = await runtime.request("thread/rename", {
              threadId: selectedThread.id,
              title,
            });
            dispatch({ type: "thread-upsert", thread: renamed.thread });
          } catch (error) {
            reportError(error);
          }
        }
      }
      return result.delivery;
    },
    [
      reportError,
      runtime,
      selectedThread,
      state.turns,
      withBusy,
    ],
  );

  const retryTurn = useCallback(
    (turnId: string) =>
      withBusy(async () => {
        const turn = state.turns.find((candidate) => candidate.id === turnId);
        if (!turn || turn.threadId !== selectedThread?.id) return;
        const snapshot = await runtime.request("turn/retry", {
          turnId,
          useCurrentSelection: false,
        });
        dispatch({ type: "snapshot", snapshot });
      }),
    [runtime, selectedThread?.id, state.turns, withBusy],
  );

  const queueTurn = useCallback(
    async (prompt: string, skillIds: string[] = []): Promise<boolean> => {
      const accepted = await withBusy(async () => {
        if (!selectedThread) return;
        const snapshot = await runtime.request("turn/enqueue", {
          threadId: selectedThread.id,
          prompt,
          messageId: `desktop-${crypto.randomUUID()}`,
          ...(skillIds.length
            ? { skills: skillIds as TurnStartParams["skills"] }
            : {}),
        });
        dispatch({ type: "snapshot", snapshot });
        return true;
      });
      return accepted === true;
    },
    [runtime, selectedThread, withBusy],
  );

  const pickWorkflowFile = useCallback(() => runtime.pickFile(), [runtime]);
  const pickContextFiles = useCallback(
    () => runtime.pickContextFiles(),
    [runtime],
  );

  const startWorkflow = useCallback(
    (
      sourceType: WorkflowStartParams["sourceType"],
      source: string,
      options: NonNullable<WorkflowStartParams["options"]>,
    ) =>
      withBusy(async () => {
        if (!selectedThread) return;
        const snapshot = await runtime.request("workflow/start", {
          threadId: selectedThread.id,
          kind: "paper2code",
          sourceType,
          source,
          options,
        });
        dispatch({ type: "workflow-snapshot", snapshot });
      }),
    [runtime, selectedThread, withBusy],
  );

  const retryWorkflow = useCallback(
    (workflowRunId: string) =>
      withBusy(async () => {
        const snapshot = await runtime.request("workflow/retry", { workflowRunId });
        dispatch({ type: "workflow-snapshot", snapshot });
      }),
    [runtime, withBusy],
  );

  const respondToWorkflow = useCallback(
    (
      workflowRunId: string,
      interactionId: string,
      action: "approve" | "modify" | "cancel",
      feedback?: string,
    ) =>
      withBusy(async () => {
        await runtime.request("workflow/respond", {
          workflowRunId,
          interactionId,
          response: {
            action,
            ...(feedback?.trim() ? { feedback: feedback.trim() } : {}),
          },
        });
      }),
    [runtime, withBusy],
  );

  const interrupt = useCallback(
    () =>
      withBusy(async () => {
        const activeWorkflow = [...state.workflows]
          .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
          .find((workflow) =>
            ["queued", "running", "waiting"].includes(workflow.status),
          );
        if (activeWorkflow) {
          await runtime.request("workflow/interrupt", {
            workflowRunId: activeWorkflow.id,
          });
          return;
        }
        const orderedTurns = [...state.turns].sort(
          (left, right) => right.ordinal - left.ordinal,
        );
        const active =
          orderedTurns.find((turn) =>
            ["running", "waiting_approval"].includes(turn.status),
          ) ??
          orderedTurns.find((turn) => turn.status === "queued");
        if (!active) return;
        const result = await runtime.request("turn/interrupt", {
          threadId: active.threadId,
          turnId: active.id,
        });
        dispatch({
          type: "snapshot",
          snapshot: { turn: result.turn, items: [], approvals: [] },
        });
      }),
    [runtime, state.turns, state.workflows, withBusy],
  );

  const interruptTurn = useCallback(
    (turnId: string) =>
      withBusy(async () => {
        const turn = state.turns.find((candidate) => candidate.id === turnId);
        if (!turn || !["queued", "running", "waiting_approval"].includes(turn.status)) {
          return;
        }
        const result = await runtime.request("turn/interrupt", {
          threadId: turn.threadId,
          turnId,
        });
        dispatch({
          type: "snapshot",
          snapshot: { turn: result.turn, items: [], approvals: [] },
        });
      }),
    [runtime, state.turns, withBusy],
  );

  const respondToApproval = useCallback(
    (approvalId: string, decision: ApprovalDecision) =>
      withBusy(async () => {
        const result = await runtime.request("approval/respond", {
          approvalId,
          decision,
        });
        dispatch({ type: "approval-upsert", approval: result.approval });
      }),
    [runtime, withBusy],
  );

  const restartRuntime = useCallback(
    () =>
      withBusy(async () => {
        loadedRuntimeRef.current = false;
        const status = await runtime.restart();
        dispatch({ type: "runtime", status });
        if (status.phase === "ready") await loadProjects();
      }),
    [loadProjects, runtime, withBusy],
  );

  return useMemo(
    () => ({
      state,
      selectedProject,
      selectedThread,
      openProject,
      selectProject,
      trustProject,
      createThread,
      forkThread,
      selectThread,
      renameThread,
      archiveThread,
      deleteThread,
      registerThread,
      setThreadModel,
      setThreadExecution,
      setAccessPreset,
      refreshSettings,
      updateSettings,
      setGoal,
      pauseGoal,
      resumeGoal,
      continueGoal,
      clearGoal,
      sendTurn,
      queueTurn,
      retryTurn,
      interruptTurn,
      pickContextFiles,
      pickWorkflowFile,
      startWorkflow,
      retryWorkflow,
      respondToWorkflow,
      interrupt,
      respondToApproval,
      selectItem: (itemId: string | null) => dispatch({ type: "select-item", itemId }),
      restartRuntime,
      dismissError: () => dispatch({ type: "error", error: null }),
    }),
    [
      createThread,
      archiveThread,
      deleteThread,
      forkThread,
      interrupt,
      interruptTurn,
      openProject,
      pickContextFiles,
      pickWorkflowFile,
      respondToApproval,
      respondToWorkflow,
      restartRuntime,
      retryWorkflow,
      renameThread,
      registerThread,
      refreshSettings,
      clearGoal,
      pauseGoal,
      resumeGoal,
      continueGoal,
      setGoal,
      setAccessPreset,
      setThreadExecution,
      setThreadModel,
      updateSettings,
      selectProject,
      selectThread,
      selectedProject,
      selectedThread,
      retryTurn,
      queueTurn,
      sendTurn,
      startWorkflow,
      state,
      trustProject,
    ],
  );
}
