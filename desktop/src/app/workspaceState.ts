import type {
  Approval,
  Event,
  Artifact,
  Item,
  Project,
  SettingsSnapshot,
  Thread,
  Turn,
  TurnSnapshotResult,
  WorkflowRun,
  WorkflowSnapshotResult,
} from "../generated/app-server";
import type { BridgeError, SidecarStatus } from "../rpc/contracts";

export interface WorkspaceState {
  runtime: SidecarStatus;
  projects: Project[];
  settings: SettingsSnapshot | null;
  threads: Thread[];
  selectedProjectId: string | null;
  selectedThreadId: string | null;
  turns: Turn[];
  items: Item[];
  approvals: Approval[];
  workflows: WorkflowRun[];
  artifacts: Artifact[];
  lastSequence: number;
  entitySequences: Record<string, number>;
  selectedItemId: string | null;
  busy: boolean;
  error: BridgeError | null;
  logs: string[];
}

export type WorkspaceAction =
  | { type: "runtime"; status: SidecarStatus }
  | { type: "projects"; projects: Project[]; selectedProjectId: string | null }
  | { type: "settings"; settings: SettingsSnapshot }
  | { type: "project-upsert"; project: Project }
  | { type: "select-project"; projectId: string | null }
  | { type: "threads"; threads: Thread[]; selectedThreadId: string | null }
  | { type: "thread-upsert"; thread: Thread }
  | { type: "thread-remove"; threadId: string }
  | { type: "select-thread"; threadId: string | null }
  | { type: "trace-reset" }
  | { type: "snapshot"; snapshot: TurnSnapshotResult }
  | { type: "approval-upsert"; approval: Approval }
  | { type: "workflow-snapshot"; snapshot: WorkflowSnapshotResult }
  | { type: "event"; event: Event }
  | { type: "select-item"; itemId: string | null }
  | { type: "busy"; busy: boolean }
  | { type: "error"; error: BridgeError | null }
  | { type: "log"; message: string };

export const initialRuntimeStatus: SidecarStatus = {
  phase: "starting",
  message: null,
  launchSource: null,
  serverInfo: null,
};

export const initialWorkspaceState: WorkspaceState = {
  runtime: initialRuntimeStatus,
  projects: [],
  settings: null,
  threads: [],
  selectedProjectId: null,
  selectedThreadId: null,
  turns: [],
  items: [],
  approvals: [],
  workflows: [],
  artifacts: [],
  lastSequence: 0,
  entitySequences: {},
  selectedItemId: null,
  busy: false,
  error: null,
  logs: [],
};

function upsert<T extends { id: string }>(values: T[], value: T): T[] {
  const existing = values.findIndex((candidate) => candidate.id === value.id);
  if (existing === -1) {
    return [...values, value];
  }
  const next = values.slice();
  next[existing] = value;
  return next;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function payloadEntity<T>(event: Event, key: string): T | null {
  const value = event.payload[key];
  return isRecord(value) ? (value as T) : null;
}

function applyItemDelta(state: WorkspaceState, event: Event): WorkspaceState {
  const itemId = event.itemId;
  const delta = event.payload.delta;
  if (!itemId || typeof delta !== "string") {
    return {
      ...state,
      lastSequence: Math.max(state.lastSequence, event.sequence),
    };
  }
  const key = `item:${itemId}`;
  if ((state.entitySequences[key] ?? 0) >= event.sequence) {
    return {
      ...state,
      lastSequence: Math.max(state.lastSequence, event.sequence),
    };
  }
  const index = state.items.findIndex((candidate) => candidate.id === itemId);
  if (index === -1) {
    return {
      ...state,
      lastSequence: Math.max(state.lastSequence, event.sequence),
    };
  }

  const current = state.items[index];
  const currentText =
    typeof current.payload.text === "string" ? current.payload.text : "";
  const summary =
    typeof event.payload.summary === "string"
      ? event.payload.summary
      : current.summary;
  const updatedAt =
    typeof event.payload.updatedAt === "string"
      ? event.payload.updatedAt
      : event.timestamp;
  const items = state.items.slice();
  items[index] = {
    ...current,
    status: "in_progress",
    summary,
    payload: {
      ...current.payload,
      text: currentText + delta,
      streaming: true,
    },
    updatedAt,
  };
  return {
    ...state,
    items,
    lastSequence: Math.max(state.lastSequence, event.sequence),
    entitySequences: {
      ...state.entitySequences,
      [key]: event.sequence,
    },
  };
}

function applyDomainEvent(state: WorkspaceState, event: Event): WorkspaceState {
  if (event.type === "item.delta") {
    return applyItemDelta(state, event);
  }
  const thread = payloadEntity<Thread>(event, "thread");
  const turn = payloadEntity<Turn>(event, "turn");
  const item = payloadEntity<Item>(event, "item");
  const approval = payloadEntity<Approval>(event, "approval");
  const workflow = payloadEntity<WorkflowRun>(event, "workflow");
  const artifact = payloadEntity<Artifact>(event, "artifact");
  const entities: Array<[string, { id: string } | null]> = [
    ["thread", thread],
    ["turn", turn],
    ["item", item],
    ["approval", approval],
    ["workflow", workflow],
    ["artifact", artifact],
  ];
  const nextSequences = { ...state.entitySequences };
  const accepts = (kind: string, entity: { id: string } | null) => {
    if (!entity) return false;
    const key = `${kind}:${entity.id}`;
    if ((nextSequences[key] ?? 0) > event.sequence) return false;
    nextSequences[key] = event.sequence;
    return true;
  };
  const accepted = new Map(
    entities.map(([kind, entity]) => [kind, accepts(kind, entity)]),
  );
  return {
    ...state,
    threads: thread && accepted.get("thread") ? upsert(state.threads, thread) : state.threads,
    turns: turn && accepted.get("turn") ? upsert(state.turns, turn) : state.turns,
    items: item && accepted.get("item") ? upsert(state.items, item) : state.items,
    approvals:
      approval && accepted.get("approval")
        ? upsert(state.approvals, approval)
        : state.approvals,
    workflows:
      workflow && accepted.get("workflow")
        ? upsert(state.workflows, workflow)
        : state.workflows,
    artifacts:
      artifact && accepted.get("artifact")
        ? upsert(state.artifacts, artifact)
        : state.artifacts,
    lastSequence: Math.max(state.lastSequence, event.sequence),
    entitySequences: nextSequences,
  };
}

function preserveEventVersion<T extends { id: string }>(
  state: WorkspaceState,
  kind: "thread" | "turn" | "item" | "approval" | "workflow" | "artifact",
  current: T[],
  incoming: T,
): T[] {
  const eventSequence = state.entitySequences[`${kind}:${incoming.id}`];
  if (eventSequence) return current;
  return upsert(current, incoming);
}

export function workspaceReducer(
  state: WorkspaceState,
  action: WorkspaceAction,
): WorkspaceState {
  switch (action.type) {
    case "runtime":
      return { ...state, runtime: action.status };
    case "projects":
      return {
        ...state,
        projects: action.projects,
        selectedProjectId: action.selectedProjectId,
      };
    case "settings":
      return { ...state, settings: action.settings };
    case "project-upsert":
      return { ...state, projects: upsert(state.projects, action.project) };
    case "select-project":
      return {
        ...state,
        selectedProjectId: action.projectId,
        selectedThreadId: null,
        turns: [],
        items: [],
        approvals: [],
        workflows: [],
        artifacts: [],
        lastSequence: 0,
        entitySequences: {},
        selectedItemId: null,
      };
    case "threads":
      return {
        ...state,
        threads: action.threads.reduce(
          (threads, thread) =>
            preserveEventVersion(state, "thread", threads, thread),
          state.threads.filter((thread) =>
            action.threads.some((incoming) => incoming.id === thread.id),
          ),
        ),
        selectedThreadId: action.selectedThreadId,
      };
    case "thread-upsert":
      return { ...state, threads: upsert(state.threads, action.thread) };
    case "thread-remove": {
      const selected = state.selectedThreadId === action.threadId;
      return {
        ...state,
        threads: state.threads.filter((thread) => thread.id !== action.threadId),
        selectedThreadId: selected ? null : state.selectedThreadId,
        turns: selected ? [] : state.turns,
        items: selected ? [] : state.items,
        approvals: selected ? [] : state.approvals,
        workflows: selected ? [] : state.workflows,
        artifacts: selected ? [] : state.artifacts,
        lastSequence: selected ? 0 : state.lastSequence,
        entitySequences: selected ? {} : state.entitySequences,
        selectedItemId: selected ? null : state.selectedItemId,
      };
    }
    case "select-thread":
      return {
        ...state,
        selectedThreadId: action.threadId,
        turns: [],
        items: [],
        approvals: [],
        workflows: [],
        artifacts: [],
        lastSequence: 0,
        entitySequences: {},
        selectedItemId: null,
      };
    case "trace-reset":
      return {
        ...state,
        turns: [],
        items: [],
        approvals: [],
        workflows: [],
        artifacts: [],
        lastSequence: 0,
        entitySequences: {},
        selectedItemId: null,
      };
    case "snapshot":
      return {
        ...state,
        turns: preserveEventVersion(
          state,
          "turn",
          state.turns,
          action.snapshot.turn,
        ),
        items: action.snapshot.items.reduce(
          (items, item) => preserveEventVersion(state, "item", items, item),
          state.items,
        ),
        approvals: action.snapshot.approvals.reduce(
          (approvals, approval) =>
            preserveEventVersion(state, "approval", approvals, approval),
          state.approvals,
        ),
      };
    case "approval-upsert":
      return {
        ...state,
        approvals: upsert(state.approvals, action.approval),
      };
    case "workflow-snapshot":
      return {
        ...state,
        workflows: preserveEventVersion(
          state,
          "workflow",
          state.workflows,
          action.snapshot.workflow,
        ),
        turns: preserveEventVersion(
          state,
          "turn",
          state.turns,
          action.snapshot.turn,
        ),
        items: action.snapshot.items.reduce(
          (items, item) => preserveEventVersion(state, "item", items, item),
          state.items,
        ),
        artifacts: action.snapshot.artifacts.reduce(
          (artifacts, artifact) =>
            preserveEventVersion(state, "artifact", artifacts, artifact),
          state.artifacts,
        ),
      };
    case "event":
      return applyDomainEvent(state, action.event);
    case "select-item":
      return { ...state, selectedItemId: action.itemId };
    case "busy":
      return { ...state, busy: action.busy };
    case "error":
      return { ...state, error: action.error };
    case "log":
      return { ...state, logs: [...state.logs.slice(-99), action.message] };
  }
}
