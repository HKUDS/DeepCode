import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  FileContent,
  FileDiff,
  FileEntry,
  GitStatus,
  MethodResults,
  TestCommand,
  Thread,
} from "../../generated/app-server";
import type { BridgeError, DesktopRuntime } from "../../rpc/contracts";

function errorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null && "message" in error) {
    return String((error as BridgeError).message);
  }
  return error instanceof Error ? error.message : String(error);
}

interface CodeWorkbenchState {
  entries: FileEntry[];
  entriesTruncated: boolean;
  git: GitStatus | null;
  diffs: FileDiff[];
  tests: TestCommand[];
  lastTestRun: MethodResults["test/run"] | null;
  file: FileContent | null;
  draft: string;
  loading: boolean;
  error: string | null;
  gitError: string | null;
  testError: string | null;
}

export interface CodeWorkbenchController extends CodeWorkbenchState {
  refresh(): Promise<void>;
  openFile(path: string): Promise<void>;
  setDraft(value: string): void;
  saveFile(): Promise<void>;
  runTest(turnId: string, commandId: string): Promise<void>;
  discardChange(file: FileDiff): Promise<void>;
  createWorktree(): Promise<void>;
  resolveWorktree(disposition: "keep" | "clean", force?: boolean): Promise<void>;
}

const initialState: CodeWorkbenchState = {
  entries: [],
  entriesTruncated: false,
  git: null,
  diffs: [],
  tests: [],
  lastTestRun: null,
  file: null,
  draft: "",
  loading: false,
  error: null,
  gitError: null,
  testError: null,
};

export function useCodeWorkbench(
  runtime: DesktopRuntime,
  thread: Thread | null,
): CodeWorkbenchController {
  const [state, setState] = useState(initialState);
  const threadId = thread?.id ?? null;
  const workspacePath = thread?.workspacePath ?? null;
  const activeThreadId = useRef<string | null>(threadId);
  const refreshGeneration = useRef(0);
  const fileGeneration = useRef(0);
  activeThreadId.current = threadId;

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    if (!threadId) {
      setState(initialState);
      return;
    }
    setState((current) => ({
      ...current,
      loading: true,
      error: null,
      gitError: null,
      testError: null,
    }));
    const [files, git, diffs, tests] = await Promise.allSettled([
      runtime.request("file/list", { threadId, depth: 4, limit: 750 }),
      runtime.request("git/status", { threadId }),
      runtime.request("git/diff", { threadId, scope: "all" }),
      runtime.request("test/discover", { threadId }),
    ]);
    if (
      generation !== refreshGeneration.current ||
      activeThreadId.current !== threadId
    ) {
      return;
    }
    setState((current) => ({
      ...current,
      entries: files.status === "fulfilled" ? files.value.entries : [],
      entriesTruncated:
        files.status === "fulfilled" ? files.value.truncated : false,
      git: git.status === "fulfilled" ? git.value.status : null,
      diffs: diffs.status === "fulfilled" ? diffs.value.files : [],
      tests: tests.status === "fulfilled" ? tests.value.commands : [],
      loading: false,
      error: files.status === "rejected" ? errorMessage(files.reason) : null,
      gitError:
        git.status === "rejected"
            ? errorMessage(git.reason)
          : diffs.status === "rejected"
            ? errorMessage(diffs.reason)
            : null,
      testError:
        tests.status === "rejected" ? errorMessage(tests.reason) : null,
    }));
  }, [runtime, threadId]);

  useEffect(() => {
    setState(initialState);
    fileGeneration.current += 1;
    void refresh();
  }, [refresh, workspacePath]);

  const openFile = useCallback(
    async (path: string) => {
      if (!threadId) return;
      const generation = ++fileGeneration.current;
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        const result = await runtime.request("file/read", {
          threadId,
          path,
          maxBytes: 128 * 1024,
        });
        if (
          generation !== fileGeneration.current ||
          activeThreadId.current !== threadId
        ) {
          return;
        }
        setState((current) => ({
          ...current,
          file: result.file,
          draft: result.file.content,
          loading: false,
        }));
      } catch (error) {
        if (
          generation !== fileGeneration.current ||
          activeThreadId.current !== threadId
        ) {
          return;
        }
        setState((current) => ({
          ...current,
          loading: false,
          error: errorMessage(error),
        }));
      }
    },
    [runtime, threadId],
  );

  const saveFile = useCallback(async () => {
    if (!threadId || !state.file || state.file.truncated) return;
    const generation = ++fileGeneration.current;
    const filePath = state.file.path;
    const savedDraft = state.draft;
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const result = await runtime.request("file/write", {
        threadId,
        path: filePath,
        content: savedDraft,
        expectedSha256: state.file.sha256,
      });
      if (
        generation !== fileGeneration.current ||
        activeThreadId.current !== threadId
      ) {
        return;
      }
      setState((current) => ({
        ...current,
        file: result.file,
        draft:
          current.draft === savedDraft ? result.file.content : current.draft,
        loading: false,
      }));
      await refresh();
    } catch (error) {
      if (
        generation !== fileGeneration.current ||
        activeThreadId.current !== threadId
      ) {
        return;
      }
      setState((current) => ({
        ...current,
        loading: false,
        error: errorMessage(error),
      }));
    }
  }, [refresh, runtime, state.draft, state.file, threadId]);

  const runTest = useCallback(
    async (turnId: string, commandId: string) => {
      if (!threadId) return;
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        const result = await runtime.request("test/run", {
          threadId,
          turnId,
          commandId,
          timeoutSeconds: 600,
        });
        if (activeThreadId.current !== threadId) return;
        setState((current) => ({
          ...current,
          lastTestRun: result,
          loading: false,
          testError: null,
        }));
        await refresh();
      } catch (error) {
        if (activeThreadId.current !== threadId) return;
        setState((current) => ({
          ...current,
          loading: false,
          testError: errorMessage(error),
        }));
      }
    },
    [refresh, runtime, threadId],
  );

  const discardChange = useCallback(
    async (file: FileDiff) => {
      if (!threadId) return;
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        await runtime.request("git/discard", {
          threadId,
          path: file.path,
          expectedRevision: file.revision,
        });
        if (activeThreadId.current !== threadId) return;
        setState((current) => ({ ...current, loading: false }));
        await refresh();
      } catch (error) {
        if (activeThreadId.current !== threadId) return;
        setState((current) => ({
          ...current,
          loading: false,
          error: errorMessage(error),
        }));
      }
    },
    [refresh, runtime, threadId],
  );

  const createWorktree = useCallback(async () => {
    if (!threadId) return;
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      await runtime.request("git/worktree/create", { threadId });
      if (activeThreadId.current !== threadId) return;
      setState((current) => ({ ...current, loading: false }));
      await refresh();
    } catch (error) {
      if (activeThreadId.current !== threadId) return;
      setState((current) => ({
        ...current,
        loading: false,
        error: errorMessage(error),
      }));
    }
  }, [refresh, runtime, threadId]);

  const resolveWorktree = useCallback(
    async (disposition: "keep" | "clean", force = false) => {
      if (!threadId) return;
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        await runtime.request("git/worktree/remove", {
          threadId,
          disposition,
          force,
          deleteBranch: false,
        });
        if (activeThreadId.current !== threadId) return;
        setState((current) => ({ ...current, loading: false }));
        await refresh();
      } catch (error) {
        if (activeThreadId.current !== threadId) return;
        setState((current) => ({
          ...current,
          loading: false,
          error: errorMessage(error),
        }));
      }
    },
    [refresh, runtime, threadId],
  );

  return useMemo(
    () => ({
      ...state,
      refresh,
      openFile,
      setDraft: (draft: string) => setState((current) => ({ ...current, draft })),
      saveFile,
      runTest,
      discardChange,
      createWorktree,
      resolveWorktree,
    }),
    [
      createWorktree,
      discardChange,
      openFile,
      refresh,
      resolveWorktree,
      runTest,
      saveFile,
      state,
    ],
  );
}
