import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useEscapeLayer } from "../../app/escapeLayer";
import { initI18n } from "../../app/i18n";
import type { Thread } from "../../generated/app-server";
import { SessionRow } from "./SessionRow";

const thread = {
  id: "thread-1",
  projectId: "project-1",
  parentThreadId: null,
  title: "Recovered task",
  mode: "code",
  status: "idle",
  model: null,
  connectionId: null,
  reasoningEffort: null,
  contextWindow: null,
  accessPresetOverride: null,
  workspacePath: "/workspace",
  worktreePath: null,
  createdAt: "2026-07-16T00:00:00Z",
  updatedAt: "2026-07-16T00:00:00Z",
  archivedAt: null,
} as Thread;

beforeEach(() => {
  initI18n();
});
afterEach(cleanup);

it("closes its menu on Escape without closing the drawer or dialog around it", () => {
  // The layer underneath, as the narrow layout's drawer registers it.
  const closeDrawer = vi.fn();
  renderHook(() => useEscapeLayer(closeDrawer, true));
  render(
    <SessionRow
      thread={thread}
      active={false}
      busy={false}
      onSelect={vi.fn()}
      onRename={vi.fn()}
      onArchive={vi.fn()}
      onDelete={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Session actions for Recovered task" }));
  expect(screen.getByRole("menu")).toBeTruthy();
  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  });

  expect(screen.queryByRole("menu")).toBeNull();
  expect(closeDrawer).not.toHaveBeenCalled();
});
