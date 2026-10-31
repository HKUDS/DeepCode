import type { Item, Turn } from "../../generated/app-server";

export interface TimelineItemEntry {
  type: "item";
  id: string;
  item: Item;
}

/** A run of steps the agent took between things it showed the user. */
export interface TimelineWorkGroup {
  type: "work_group";
  id: string;
  items: Item[];
}

export type TimelineEntry = TimelineItemEntry | TimelineWorkGroup;

export interface ConversationTurn {
  id: string;
  turn: Turn | null;
  userMessages: Item[];
  timeline: TimelineEntry[];
  completion: Item | null;
}

interface MutableConversationTurn
  extends Omit<ConversationTurn, "timeline"> {
  timelineItems: Item[];
  sortOrdinal: number;
}

function createGroup(turn: Turn): MutableConversationTurn {
  return {
    id: turn.id,
    turn,
    userMessages: [],
    timelineItems: [],
    completion: null,
    sortOrdinal: turn.ordinal,
  };
}

/** Who asked for a Turn whose prompt the Goal runtime wrote. */
export type GoalPrompt = "automatic" | "requested";

/**
 * Whether a user message is the Goal runtime's instruction to the model
 * rather than words the user typed, and who asked for it. Mirrors
 * `goal_prompt` in `core/domain/message_provenance.py`: a retry carries the
 * first retried Turn's provenance under `retryOf`.
 */
export function goalPrompt(item: Item): GoalPrompt | null {
  if (item.kind !== "user_message") return null;
  const { source, client, retryOf } = item.payload;
  if (source === "retry") {
    const retried =
      typeof retryOf === "object" && retryOf !== null && !Array.isArray(retryOf)
        ? retryOf
        : null;
    return retried?.source === "goal_continuation" ? "requested" : null;
  }
  if (source !== "goal_continuation") return null;
  return client === "internal" ? "automatic" : "requested";
}

/** How the backend described a tool call (`ToolActivity` in core/events/protocol.py). */
export interface ItemActivity {
  kind: string;
  label: string;
  subject: string | null;
}

export function itemActivity(item: Item): ItemActivity | null {
  const value = item.payload.activity;
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    typeof value.kind !== "string" ||
    typeof value.label !== "string"
  ) {
    return null;
  }
  return {
    kind: value.kind,
    label: value.label,
    subject: typeof value.subject === "string" ? value.subject : null,
  };
}

// Steps the agent takes on its way. Everything else stays in place for the
// user to see: what the agent says, approvals, errors, plans, artifacts, and
// test results (the user runs those, after the Turn).
const workKinds = new Set<Item["kind"]>([
  "reasoning_summary",
  "command_execution",
  "tool_call",
  "file_change",
  "diff",
]);

const tallyOrder = ["commands", "edits", "reads", "searches", "tools"] as const;

/** What a step did, for a work group's tally. Thinking is not an action. */
export type WorkTally = (typeof tallyOrder)[number];

function workTally(item: Item): WorkTally | null {
  switch (item.kind) {
    case "reasoning_summary":
      return null;
    case "file_change":
    case "diff":
      return "edits";
  }
  switch (itemActivity(item)?.kind) {
    case "read":
      return "reads";
    case "search":
    case "list":
      return "searches";
    case "edit":
      return "edits";
    case "run":
      return "commands";
  }
  return item.kind === "command_execution" ? "commands" : "tools";
}

// A run of work folds into one group once it holds this many actions; a
// single action reads fine on its own.
const MIN_GROUPED_ACTIONS = 2;

function compactTimeline(items: Item[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  let run: Item[] = [];
  const endRun = () => {
    const actions = run.filter((item) => workTally(item) !== null).length;
    if (actions >= MIN_GROUPED_ACTIONS) {
      // Keyed by its first step, so the group keeps its identity (and its
      // open state) while later steps stream into it.
      entries.push({ type: "work_group", id: `work:${run[0].id}`, items: run });
    } else {
      for (const item of run) entries.push({ type: "item", id: item.id, item });
    }
    run = [];
  };

  for (const item of items) {
    if (workKinds.has(item.kind)) {
      run.push(item);
      continue;
    }
    endRun();
    entries.push({ type: "item", id: item.id, item });
  }
  endRun();
  return entries;
}

export interface WorkSummary {
  /** Actions by kind, in display order; kinds with none are left out. */
  tallies: [WorkTally, number][];
  /** Failed actions. A stopped Turn also fails its open thinking, which is no action. */
  failed: number;
  /** The step running now, if the group is still at work. */
  current: Item | null;
  /** From the first step's start to the last update; null if unknown. */
  seconds: number | null;
}

export function summarizeWork(items: readonly Item[]): WorkSummary {
  const counts = new Map<WorkTally, number>();
  let failed = 0;
  let current: Item | null = null;
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const item of items) {
    const tally = workTally(item);
    if (tally) {
      counts.set(tally, (counts.get(tally) ?? 0) + 1);
      if (item.status === "failed") failed += 1;
    }
    if (item.status === "in_progress") current = item;
    const created = Date.parse(item.createdAt);
    const updated = Date.parse(item.updatedAt);
    if (Number.isFinite(created)) start = Math.min(start, created);
    if (Number.isFinite(updated)) end = Math.max(end, updated);
  }
  return {
    tallies: tallyOrder.flatMap((tally): [WorkTally, number][] => {
      const count = counts.get(tally);
      return count ? [[tally, count]] : [];
    }),
    failed,
    current,
    seconds:
      Number.isFinite(start) && Number.isFinite(end)
        ? Math.max(0, Math.round((end - start) / 1000))
        : null,
  };
}

export function buildConversationTurns(
  turns: Turn[],
  items: Item[],
): ConversationTurn[] {
  const groups = new Map(
    turns.map((turn) => [turn.id, createGroup(turn)]),
  );
  let orphanOrdinal = turns.reduce(
    (largest, turn) => Math.max(largest, turn.ordinal),
    0,
  );

  for (const item of [...items].sort((left, right) => {
    const leftTurn = groups.get(left.turnId)?.sortOrdinal ?? Number.MAX_SAFE_INTEGER;
    const rightTurn =
      groups.get(right.turnId)?.sortOrdinal ?? Number.MAX_SAFE_INTEGER;
    return leftTurn - rightTurn || left.ordinal - right.ordinal;
  })) {
    let group = groups.get(item.turnId);
    if (!group) {
      orphanOrdinal += 1;
      group = {
        id: item.turnId,
        turn: null,
        userMessages: [],
        timelineItems: [],
        completion: null,
        sortOrdinal: orphanOrdinal,
      };
      groups.set(item.turnId, group);
    }
    switch (item.kind) {
      case "user_message":
        group.userMessages.push(item);
        break;
      case "completion":
        group.completion = item;
        break;
      default:
        group.timelineItems.push(item);
    }
  }

  return [...groups.values()]
    .sort((left, right) => left.sortOrdinal - right.sortOrdinal)
    .map((group) => ({
      id: group.id,
      turn: group.turn,
      userMessages: group.userMessages,
      timeline: compactTimeline(group.timelineItems),
      completion: group.completion,
    }));
}

/**
 * What a Turn costs to show in the normal transcript, in rows: a folded run
 * of steps is one row, so a Turn of hundreds of steps is cheap while its runs
 * stay folded. Without a user message item, the Turn's prompt is shown.
 */
function turnRows(group: ConversationTurn): number {
  const asked = group.userMessages.length || (group.turn?.prompt ? 1 : 0);
  return asked + group.timeline.length + (group.completion ? 1 : 0);
}

/**
 * Where a batch of history ending before `end` starts: as many Turns as fit
 * in `budget` rows, counting back from `end`, and always at least one.
 */
export function historyBatchStart(
  groups: readonly ConversationTurn[],
  end: number,
  budget: number,
): number {
  let start = end;
  let rows = 0;
  while (start > 0) {
    rows += turnRows(groups[start - 1]);
    if (rows > budget && start < end) break;
    start -= 1;
  }
  return start;
}

export function turnDurationSeconds(
  turn: Turn | null,
  now = Date.now(),
): number | null {
  if (!turn?.startedAt) return null;
  const start = new Date(turn.startedAt).getTime();
  const end = turn.completedAt ? new Date(turn.completedAt).getTime() : now;
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return Math.max(0, Math.round((end - start) / 1000));
}

export function formatTurnDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (minutes < 60) return `${minutes}m ${remainder.toString().padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  const minuteRemainder = minutes % 60;
  return `${hours}h ${minuteRemainder.toString().padStart(2, "0")}m`;
}
