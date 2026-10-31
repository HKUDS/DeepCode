import i18n from "i18next";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { __setLocaleForTests, initI18n } from "../../app/i18n";
import type {
  Approval,
  ApprovalStatus,
  Item,
  ItemKind,
  ItemStatus,
} from "../../generated/app-server";
import {
  goalStatusLabel,
  type GoalPresentationStatus,
} from "../goal/goalPresentation";
import {
  activityLabel,
  approvalCategoryLabel,
  approvalOutcome,
  itemStatusText,
  presentItem,
} from "./itemPresentation";

// Every value of each type: a value added to the protocol without a label
// here fails the typecheck, and without a translation fails below.
const ITEM_KINDS = Object.keys({
  user_message: true,
  assistant_message: true,
  reasoning_summary: true,
  plan: true,
  tool_call: true,
  command_execution: true,
  file_change: true,
  diff: true,
  test_result: true,
  approval_request: true,
  workflow_stage: true,
  artifact: true,
  error: true,
  completion: true,
} satisfies Record<ItemKind, true>) as ItemKind[];
const ITEM_STATUSES = Object.keys({
  pending: true,
  in_progress: true,
  completed: true,
  failed: true,
  declined: true,
} satisfies Record<ItemStatus, true>) as ItemStatus[];
const APPROVAL_STATUSES = Object.keys({
  pending: true,
  approved_once: true,
  approved_session: true,
  denied: true,
  cancelled: true,
  expired: true,
} satisfies Record<ApprovalStatus, true>) as ApprovalStatus[];
const APPROVAL_CATEGORIES = Object.keys({
  command: true,
  file_write: true,
  network: true,
  external_tool: true,
  destructive: true,
} satisfies Record<Approval["category"], true>) as Approval["category"][];
const GOAL_STATUSES = Object.keys({
  working: true,
  readyToContinue: true,
  finishing: true,
  paused: true,
  blocked: true,
  budget_limited: true,
  complete: true,
} satisfies Record<GoalPresentationStatus, true>) as GoalPresentationStatus[];

function item(kind: ItemKind): Item {
  return {
    id: `item-${kind}`,
    threadId: "thread-1",
    turnId: "turn-1",
    ordinal: 1,
    kind,
    status: "completed",
    summary: "summary",
    payload: {},
    createdAt: "2026-10-31T00:00:00Z",
    updatedAt: "2026-10-31T00:00:00Z",
  };
}

/** Words a Chinese reader should not meet; Turn is kept as a product term. */
function englishWords(text: string): string[] {
  return (text.match(/[A-Za-z]{2,}/g) ?? []).filter((word) => word !== "Turn");
}

beforeEach(() => {
  initI18n();
});
afterEach(() => {
  __setLocaleForTests("en");
});

describe("labels in zh-CN", () => {
  beforeEach(() => __setLocaleForTests("zh-CN"));
  const t = () => i18n.t.bind(i18n);

  it.each(ITEM_KINDS)("names a %s item in Chinese", (kind) => {
    const shown = presentItem(item(kind), t());
    expect(englishWords(shown.stage)).toEqual([]);
    expect(englishWords(shown.label)).toEqual([]);
  });

  it("names every item status, approval outcome and category in Chinese", () => {
    const labels = [
      ...ITEM_STATUSES.map((status) => itemStatusText(status, t())),
      ...APPROVAL_STATUSES.map((status) => approvalOutcome(status, t())),
      ...APPROVAL_CATEGORIES.map((category) => approvalCategoryLabel(category, t())),
      ...GOAL_STATUSES.map((status) => goalStatusLabel(status, t())),
      ...["read", "search", "list", "run", "edit", "plan"].map((kind) =>
        activityLabel({ kind, label: "Built-in" }, t()),
      ),
    ];
    expect(labels.filter((label) => englishWords(label).length)).toEqual([]);
  });
});

it("reads approval outcomes as sentences in English, not raw values", () => {
  __setLocaleForTests("en");
  const t = i18n.t.bind(i18n);
  expect(APPROVAL_STATUSES.map((status) => approvalOutcome(status, t))).toEqual([
    "Waiting for approval",
    "Allowed once",
    "Allowed for this Session",
    "Denied",
    "Cancelled",
    "Expired",
  ]);
  expect(approvalCategoryLabel("file_write", t)).toBe("File change");
});

it("keeps the name of a tool that is not built in", () => {
  __setLocaleForTests("zh-CN");
  const t = i18n.t.bind(i18n);
  expect(activityLabel({ kind: "other", label: "Get Goal" }, t)).toBe("Get Goal");
  expect(activityLabel({ kind: "run", label: "Run" }, t)).toBe("运行");
});
