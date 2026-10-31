import type { TFunction } from "i18next";

import type { Approval, Item } from "../../generated/app-server";

interface ItemPresentation {
  stage: string;
  label: string;
  body: string | null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** An error item for a problem the Turn survived, not for its failure. */
export function isWarning(item: Item): boolean {
  return item.kind === "error" && item.payload.severity === "warning";
}

/** How an item reads to the user: its stage, its label and its text. */
export function presentItem(item: Item, t: TFunction): ItemPresentation {
  const text = stringValue(item.payload.text);
  const result = stringValue(item.payload.resultPreview);
  switch (item.kind) {
    case "user_message":
      return {
        stage: t("item.stage.intent", "Intent"),
        label: t("item.kind.request", "Request"),
        body: text ?? item.summary,
      };
    case "assistant_message":
      return {
        stage: t("item.stage.response", "Response"),
        label: t("item.kind.agent", "Agent"),
        body: text ?? item.summary,
      };
    case "reasoning_summary":
      return {
        stage: t("item.stage.reasoning", "Reasoning"),
        label: t("item.kind.thinking", "Thinking"),
        body:
          stringValue(item.payload.summaryText) ??
          stringValue(item.payload.traceText) ??
          text ??
          result ??
          item.summary,
      };
    case "plan":
      return {
        stage: t("item.stage.plan", "Plan"),
        label: t("item.kind.plan", "Execution plan"),
        body: text ?? result ?? item.summary,
      };
    case "approval_request":
      return {
        stage: t("item.stage.approval", "Approval"),
        label: t("item.kind.approval", "Permission review"),
        body: item.summary,
      };
    case "command_execution":
      return {
        stage: t("item.stage.tool", "Tool"),
        label: t("item.kind.command", "Command"),
        body: result ?? item.summary,
      };
    case "tool_call":
      return {
        stage: t("item.stage.tool", "Tool"),
        label: t("item.kind.toolCall", "Tool call"),
        body: result ?? item.summary,
      };
    case "file_change":
      return {
        stage: t("item.stage.change", "Change"),
        label: t("item.kind.fileChange", "File change"),
        body: result ?? item.summary,
      };
    case "diff":
      return {
        stage: t("item.stage.change", "Change"),
        label: t("item.kind.diff", "Diff"),
        body: text ?? item.summary,
      };
    case "test_result":
      return {
        stage: t("item.stage.verify", "Verify"),
        label: t("item.kind.testResult", "Test result"),
        body: result ?? item.summary,
      };
    case "artifact":
      return {
        stage: t("item.stage.artifact", "Artifact"),
        label: t("item.kind.artifact", "Artifact"),
        body: item.summary,
      };
    case "workflow_stage":
      return {
        stage: t("item.stage.workflow", "Workflow"),
        label: t("item.kind.workflowStage", "Workflow stage"),
        body: item.summary,
      };
    case "error":
      if (isWarning(item))
        return {
          stage: t("item.stage.warning", "Warning"),
          label: t("item.kind.warning", "Warning"),
          body: text ?? item.summary,
        };
      return {
        stage: t("item.stage.error", "Error"),
        label: t("item.kind.error", "Execution error"),
        body: text ?? item.summary,
      };
    case "completion":
      return {
        stage: t("item.stage.complete", "Complete"),
        label: t("item.kind.completion", "Turn complete"),
        body: item.summary,
      };
  }
}

/**
 * What a tool step did, in words. Built-in tools are named by their kind of
 * activity; any other tool keeps its own name, as the service gives it.
 */
export function activityLabel(activity: { kind: string; label: string }, t: TFunction): string {
  switch (activity.kind) {
    case "read":
      return t("activity.read", "Read");
    case "search":
      return t("activity.search", "Search");
    case "list":
      return t("activity.list", "List");
    case "run":
      return t("activity.run", "Run");
    case "edit":
      return t("activity.edit", "Edit");
    case "plan":
      return t("activity.plan", "Update plan");
    default:
      return activity.label;
  }
}

/** An item's status, in words. */
export function itemStatusText(status: Item["status"], t: TFunction): string {
  switch (status) {
    case "pending":
      return t("turn.itemStatus.pending", "pending");
    case "in_progress":
      return t("turn.itemStatus.inProgress", "in progress");
    case "completed":
      return t("turn.itemStatus.completed", "completed");
    case "failed":
      return t("turn.itemStatus.failed", "failed");
    case "declined":
      return t("turn.itemStatus.declined", "declined");
  }
}

/** What happened to an approval, in words. */
export function approvalOutcome(status: Approval["status"], t: TFunction): string {
  switch (status) {
    case "pending":
      return t("turn.approval.pending", "Waiting for approval");
    case "approved_once":
      return t("turn.approval.allowedOnce", "Allowed once");
    case "approved_session":
      return t("turn.approval.allowedSession", "Allowed for this Session");
    case "denied":
      return t("turn.approval.denied", "Denied");
    case "cancelled":
      return t("turn.approval.cancelled", "Cancelled");
    case "expired":
      return t("turn.approval.expired", "Expired");
  }
}

/** The kind of operation an approval guards, in words. */
export function approvalCategoryLabel(
  category: Approval["category"],
  t: TFunction,
): string {
  switch (category) {
    case "command":
      return t("approval.category.command", "Command");
    case "file_write":
      return t("approval.category.fileWrite", "File change");
    case "network":
      return t("approval.category.network", "Network access");
    case "external_tool":
      return t("approval.category.externalTool", "External tool");
    case "destructive":
      return t("approval.category.destructive", "Destructive operation");
  }
}

export function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}
