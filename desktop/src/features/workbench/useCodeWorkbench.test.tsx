import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, it } from "vitest";

import type {
  FileContent,
  FileDiff,
  FileEntry,
  Item,
  MethodParams,
  MethodResults,
  Thread,
} from "../../generated/app-server";
import type {
  AnyRpcNotification,
  DesktopRuntime,
  RpcMethod,
  SidecarStatus,
} from "../../rpc/contracts";
import { useCodeWorkbench } from "./useCodeWorkbench";

const ready: SidecarStatus = {
  phase: "ready",
  message: null,
  launchSource: "test",
  serverInfo: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function file(path: string): FileEntry {
  return {
    path,
    name: path,
    kind: "file",
    size: 1,
    modifiedAt: null,
    hidden: false,
  };
}

function fileContent(content: string, sha256: string): FileContent {
  return {
    path: "src/main.ts",
    content,
    byteSize: new TextEncoder().encode(content).byteLength,
    sha256,
    lineCount: content.split("\n").length,
    truncated: false,
  };
}

const changedFile: FileDiff = {
  path: "src/main.ts",
  originalPath: null,
  status: "modified",
  binary: false,
  additions: 1,
  deletions: 1,
  revision: "diff-revision",
  hunks: [],
};

function thread(id: string): Thread {
  return {
    id,
    projectId: "project-1",
    parentThreadId: null,
    title: id,
    mode: "code",
    status: "idle",
    model: null,
    connectionId: null,
    reasoningEffort: null,
    accessPresetOverride: null,
    workspacePath: `/workspace/${id}`,
    worktreePath: null,
    createdAt: "2026-07-16T00:00:00Z",
    updatedAt: "2026-07-16T00:00:00Z",
    archivedAt: null,
  };
}

const testItem: Item = {
  id: "item-test",
  threadId: "thread-b",
  turnId: "turn-completed",
  ordinal: 2,
  kind: "test_result",
  status: "completed",
  summary: "Node tests passed",
  payload: {},
  createdAt: "2026-07-16T00:00:00Z",
  updatedAt: "2026-07-16T00:00:00Z",
};

class WorkbenchRuntime implements DesktopRuntime {
  readonly firstFiles = deferred<MethodResults["file/list"]>();

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    const identity = params as { threadId?: string };
    switch (method) {
      case "file/list":
        if (identity.threadId === "thread-a") {
          return this.firstFiles.promise as Promise<MethodResults[M]>;
        }
        return { entries: [file("b.txt")], truncated: false } as MethodResults[M];
      case "git/status":
        return {
          status: {
            repositoryRoot: "/workspace",
            branch: "main",
            upstream: null,
            ahead: 0,
            behind: 0,
            detached: false,
            entries: [],
          },
        } as unknown as MethodResults[M];
      case "git/diff":
        return { files: [] } as unknown as MethodResults[M];
      case "test/discover":
        return { commands: [] } as unknown as MethodResults[M];
      default:
        throw new Error(`Unexpected workbench request: ${method}`);
    }
  }

  async status() {
    return ready;
  }

  async restart() {
    return ready;
  }

  async pickDirectory() {
    return null;
  }

  async pickFile() {
    return null;
  }

  async pickContextFiles() {
    return [];
  }

  async exportDiagnostics() {
    return null;
  }

  async checkForUpdate() {
    return null;
  }

  async installUpdate() {
    return undefined;
  }

  async onNotification(listener: (notification: AnyRpcNotification) => void) {
    void listener;
    return () => undefined;
  }

  async onStatus(listener: (status: SidecarStatus) => void) {
    void listener;
    return () => undefined;
  }

  async onLog(listener: (message: string) => void) {
    void listener;
    return () => undefined;
  }
}

it("does not let a slow previous Thread overwrite the active workbench", async () => {
  const runtime = new WorkbenchRuntime();
  const { result, rerender } = renderHook(
    ({ selected }) => useCodeWorkbench(runtime, selected),
    { initialProps: { selected: thread("thread-a") } },
  );

  rerender({ selected: thread("thread-b") });
  await waitFor(() => expect(result.current.entries[0]?.path).toBe("b.txt"));

  await act(async () => {
    runtime.firstFiles.resolve({ entries: [file("a.txt")], truncated: false });
    await runtime.firstFiles.promise;
  });
  expect(result.current.entries[0]?.path).toBe("b.txt");
});

class MutationRuntime implements DesktopRuntime {
  readonly calls: Array<{ method: RpcMethod; params: unknown }> = [];
  readonly writeResult = deferred<MethodResults["file/write"]>();
  fileListCount = 0;

  async request<M extends RpcMethod>(
    method: M,
    params: MethodParams[M],
  ): Promise<MethodResults[M]> {
    this.calls.push({ method, params });
    switch (method) {
      case "file/list":
        this.fileListCount += 1;
        return {
          entries: [file("src/main.ts")],
          truncated: true,
        } as MethodResults[M];
      case "file/read":
        return {
          file: fileContent("const value = 1;\n", "sha-before"),
        } as MethodResults[M];
      case "file/write":
        return this.writeResult.promise as Promise<MethodResults[M]>;
      case "git/status":
        return {
          status: {
            repositoryRoot: "/workspace/thread-b",
            branch: "main",
            upstream: null,
            ahead: 0,
            behind: 0,
            detached: false,
            entries: [],
          },
        } as unknown as MethodResults[M];
      case "git/diff":
        return { files: [changedFile] } as MethodResults[M];
      case "git/discard":
        return {
          discarded: true,
          path: changedFile.path,
        } as MethodResults[M];
      case "git/worktree/create":
      case "git/worktree/remove":
        return {
          thread: {
            ...thread("thread-b"),
            workspacePath: "/workspace/worktree",
            worktreePath: "/workspace/worktree",
          },
          path: "/workspace/worktree",
          branch: "deepcode/thread-b",
          disposition:
            method === "git/worktree/create" ? "created" : "cleaned",
          dirty: false,
        } as MethodResults[M];
      case "test/discover":
        return {
          commands: [
            {
              id: "npm-test",
              label: "Node tests",
              argv: ["npm", "test"],
            },
          ],
        } as MethodResults[M];
      case "test/run":
        return {
          item: testItem,
          command: {
            id: "npm-test",
            label: "Node tests",
            argv: ["npm", "test"],
          },
          exitCode: 0,
          timedOut: false,
          durationMs: 42,
          stdout: "passed\n",
          stderr: "",
          outputTruncated: false,
        } as MethodResults[M];
      default:
        throw new Error(`Unexpected workbench request: ${method}`);
    }
  }

  async status() {
    return ready;
  }

  async restart() {
    return ready;
  }

  async pickDirectory() {
    return null;
  }

  async pickFile() {
    return null;
  }

  async pickContextFiles() {
    return [];
  }

  async exportDiagnostics() {
    return null;
  }

  async checkForUpdate() {
    return null;
  }

  async installUpdate() {
    return undefined;
  }

  async onNotification(listener: (notification: AnyRpcNotification) => void) {
    void listener;
    return () => undefined;
  }

  async onStatus(listener: (status: SidecarStatus) => void) {
    void listener;
    return () => undefined;
  }

  async onLog(listener: (message: string) => void) {
    void listener;
    return () => undefined;
  }
}

it("uses the file revision and preserves edits typed while a save is in flight", async () => {
  const runtime = new MutationRuntime();
  const { result } = renderHook(() =>
    useCodeWorkbench(runtime, thread("thread-b")),
  );

  await waitFor(() => expect(result.current.entries).toHaveLength(1));
  await act(async () => {
    await result.current.openFile("src/main.ts");
  });
  act(() => result.current.setDraft("const value = 2;\n"));

  let save: Promise<void>;
  act(() => {
    save = result.current.saveFile();
  });
  await waitFor(() =>
    expect(runtime.calls.some((call) => call.method === "file/write")).toBe(true),
  );
  act(() => result.current.setDraft("const value = 3;\n"));
  runtime.writeResult.resolve({
    file: fileContent("const value = 2;\n", "sha-after"),
  });
  await act(async () => {
    await save;
  });

  const write = runtime.calls.find((call) => call.method === "file/write");
  expect(write?.params).toEqual({
    threadId: "thread-b",
    path: "src/main.ts",
    content: "const value = 2;\n",
    expectedSha256: "sha-before",
  });
  expect(result.current.file?.content).toBe("const value = 2;\n");
  expect(result.current.draft).toBe("const value = 3;\n");
  expect(result.current.entriesTruncated).toBe(true);
  expect(runtime.fileListCount).toBeGreaterThanOrEqual(2);
});

it("passes exact mutation identities and retains the latest verification result", async () => {
  const runtime = new MutationRuntime();
  const { result } = renderHook(() =>
    useCodeWorkbench(runtime, thread("thread-b")),
  );

  await waitFor(() => expect(result.current.diffs).toHaveLength(1));
  await act(async () => {
    await result.current.discardChange(changedFile);
    await result.current.createWorktree();
    await result.current.resolveWorktree("clean", true);
    await result.current.runTest("turn-completed", "npm-test");
  });

  expect(
    runtime.calls.find((call) => call.method === "git/discard")?.params,
  ).toEqual({
    threadId: "thread-b",
    path: "src/main.ts",
    expectedRevision: "diff-revision",
  });
  expect(
    runtime.calls.find((call) => call.method === "git/worktree/remove")?.params,
  ).toEqual({
    threadId: "thread-b",
    disposition: "clean",
    force: true,
    deleteBranch: false,
  });
  expect(runtime.calls.find((call) => call.method === "test/run")?.params).toEqual({
    threadId: "thread-b",
    turnId: "turn-completed",
    commandId: "npm-test",
    timeoutSeconds: 600,
  });
  expect(result.current.lastTestRun?.item.id).toBe("item-test");
  expect(result.current.lastTestRun?.stdout).toBe("passed\n");
});
