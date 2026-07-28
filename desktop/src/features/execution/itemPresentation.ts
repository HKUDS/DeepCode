import type { Item } from "../../generated/app-server";

interface ItemPresentation {
  stage: string;
  label: string;
  body: string | null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function presentItem(item: Item): ItemPresentation {
  const text = stringValue(item.payload.text);
  const result = stringValue(item.payload.resultPreview);
  switch (item.kind) {
    case "user_message":
      return { stage: "Intent", label: "Request", body: text ?? item.summary };
    case "assistant_message":
      return { stage: "Response", label: "Agent", body: text ?? item.summary };
    case "reasoning_summary":
      return {
        stage: "Reasoning",
        label: "Thinking",
        body:
          stringValue(item.payload.summaryText) ??
          stringValue(item.payload.traceText) ??
          text ??
          result ??
          item.summary,
      };
    case "plan":
      return {
        stage: "Plan",
        label: "Execution plan",
        body: text ?? result ?? item.summary,
      };
    case "approval_request":
      return { stage: "Approval", label: "Permission review", body: item.summary };
    case "command_execution":
      return { stage: "Tool", label: "Command", body: result ?? item.summary };
    case "tool_call":
      return { stage: "Tool", label: "Tool call", body: result ?? item.summary };
    case "file_change":
      return { stage: "Change", label: "File change", body: result ?? item.summary };
    case "diff":
      return { stage: "Change", label: "Diff", body: text ?? item.summary };
    case "test_result":
      return { stage: "Verify", label: "Test result", body: result ?? item.summary };
    case "artifact":
      return { stage: "Artifact", label: "Artifact", body: item.summary };
    case "workflow_stage":
      return { stage: "Workflow", label: "Workflow stage", body: item.summary };
    case "error":
      return { stage: "Error", label: "Execution error", body: text ?? item.summary };
    case "completion":
      return { stage: "Complete", label: "Turn complete", body: item.summary };
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
