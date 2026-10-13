import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkflowRun } from "../../generated/app-server";
import { __setLocaleForTests, initI18n } from "../../app/i18n";
import { WorkflowComposer } from "./WorkflowComposer";

initI18n();

const waiting: WorkflowRun = {
  id: "workflow-1",
  threadId: "thread-1",
  turnId: "turn-1",
  kind: "paper2code",
  status: "waiting",
  input: {},
  result: {},
  attempt: 2,
  retryOf: null,
  currentStage: "plan_review",
  progressCurrent: 1,
  progressTotal: 4,
  checkpoint: { interaction: { id: "interaction-1", request: {} } },
  createdAt: "2026-07-23T01:00:00Z",
  updatedAt: "2026-07-23T01:00:00Z",
  startedAt: "2026-07-23T01:00:00Z",
  completedAt: null,
  errorCode: null,
  errorMessage: null,
};

function composer(workflow: WorkflowRun | null) {
  return (
    <WorkflowComposer
      enabled
      busy={false}
      workflow={workflow}
      disabledReason={null}
      onPickFile={vi.fn(async () => null)}
      onStart={vi.fn(async () => undefined)}
      onRetry={vi.fn(async () => undefined)}
      onRespond={vi.fn(async () => undefined)}
      onInterrupt={vi.fn()}
    />
  );
}

describe("WorkflowComposer", () => {
  afterEach(() => {
    cleanup();
    __setLocaleForTests("en");
  });

  it("shows the Paper2Code controls in the interface language", () => {
    __setLocaleForTests("zh-CN");
    const { unmount } = render(composer(null));
    expect(screen.getByRole("tablist", { name: "输入来源" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "文档" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "开始 Paper2Code" })).toBeTruthy();
    expect(
      screen.getByText("生成的代码必须通过发现的测试才算完成。"),
    ).toBeTruthy();
    unmount();

    render(composer(waiting));
    expect(screen.getByText("Paper2Code · 第 2 次尝试")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "审查实现计划" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "批准并继续" })).toBeTruthy();
  });
});
