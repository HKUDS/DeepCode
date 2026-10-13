import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileDiff, Thread } from "../../generated/app-server";
import type { DesktopInspectorTab } from "../../app/useDesktopUi";
import type { ClientRuntime } from "../../rpc/contracts";
import { Inspector } from "./Inspector";

vi.mock("../workbench/LocalMonacoEditor", () => ({
  default: ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?(value: string | undefined): void;
  }) => (
    <textarea
      aria-label="File editor"
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}));

const thread: Thread = {
  id: "thread-1",
  projectId: "project-1",
  parentThreadId: null,
  title: "Thread",
  mode: "code",
  status: "idle",
  model: null,
  connectionId: null,
  reasoningEffort: null,
  contextWindow: null,
  accessPresetOverride: null,
  workspacePath: "/workspace/thread-1",
  worktreePath: null,
  createdAt: "2026-07-16T00:00:00Z",
  updatedAt: "2026-07-16T00:00:00Z",
  archivedAt: null,
};

function diff(path: string): FileDiff {
  return {
    path,
    originalPath: null,
    status: "modified",
    binary: false,
    additions: 1,
    deletions: 1,
    revision: `rev-${path}`,
    hunks: [],
  };
}

function workbenchRuntime() {
  const request = vi.fn(async (method: string, params: { path?: string }) => {
    switch (method) {
      case "file/list":
        return {
          entries: [
            {
              path: "main.ts",
              name: "main.ts",
              kind: "file",
              size: 1,
              modifiedAt: null,
              hidden: false,
            },
          ],
          truncated: false,
        };
      case "git/status":
        return { status: null };
      case "git/diff":
        return { files: [diff("main.ts"), diff("other.ts")] };
      case "test/discover":
        return { commands: [] };
      case "file/read":
        return {
          file: {
            path: params.path,
            content: `saved ${params.path}`,
            byteSize: 1,
            sha256: "sha",
            lineCount: 1,
            truncated: false,
          },
        };
      default:
        throw new Error(method);
    }
  });
  return { request, runtime: { request } as unknown as ClientRuntime };
}

function TestInspector({ runtime }: { runtime: ClientRuntime }) {
  const [tab, setTab] = useState<DesktopInspectorTab>("files");
  return (
    <Inspector
      runtime={runtime}
      thread={thread}
      trusted
      turns={[]}
      items={[]}
      workflows={[]}
      artifacts={[]}
      selectedItemId={null}
      tab={tab}
      onSelectItem={vi.fn()}
      onTabChange={setTab}
      onDirtyChange={vi.fn()}
      onClose={vi.fn()}
    />
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Inspector", () => {
  it("keeps an unsaved draft when a change is opened in the editor", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { request, runtime } = workbenchRuntime();
    render(<TestInspector runtime={runtime} />);

    fireEvent.click(await screen.findByRole("button", { name: /main\.ts/ }));
    const editor = (await screen.findByLabelText(
      "File editor",
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toBe("saved main.ts"));
    fireEvent.change(editor, { target: { value: "my edit" } });

    // The open file itself: back to its draft, nothing to confirm.
    fireEvent.click(screen.getByRole("tab", { name: /changes/ }));
    fireEvent.click(
      (await screen.findAllByRole("button", { name: "Open in editor" }))[0],
    );
    expect(
      ((await screen.findByLabelText("File editor")) as HTMLTextAreaElement)
        .value,
    ).toBe("my edit");
    expect(confirm).not.toHaveBeenCalled();

    // Another file asks first, and declining keeps the draft.
    fireEvent.click(screen.getByRole("tab", { name: /changes/ }));
    fireEvent.click(
      (await screen.findAllByRole("button", { name: "Open in editor" }))[1],
    );
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(
      ((await screen.findByLabelText("File editor")) as HTMLTextAreaElement)
        .value,
    ).toBe("my edit");
    expect(
      request.mock.calls.filter(([method]) => method === "file/read"),
    ).toHaveLength(1);
  });
});
