import type { Item, Turn } from "../../generated/app-server";

export interface TimelineItemEntry {
  type: "item";
  id: string;
  item: Item;
}

export interface TimelineActivityGroup {
  type: "activity_group";
  id: string;
  activityKind: "exploration";
  items: Item[];
}

export type TimelineEntry = TimelineItemEntry | TimelineActivityGroup;

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

const groupedActivityKinds = new Set(["read", "search", "list"]);

function itemActivityKind(item: Item): string | null {
  const activity = item.payload.activity;
  if (
    typeof activity !== "object" ||
    activity === null ||
    Array.isArray(activity)
  ) {
    return null;
  }
  return typeof activity.kind === "string" ? activity.kind : null;
}

function canJoinExplorationGroup(item: Item): boolean {
  const activityKind = itemActivityKind(item);
  return activityKind !== null && groupedActivityKinds.has(activityKind);
}

function compactTimeline(items: Item[]): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  let index = 0;

  while (index < items.length) {
    const item = items[index];
    if (!canJoinExplorationGroup(item)) {
      entries.push({ type: "item", id: item.id, item });
      index += 1;
      continue;
    }

    const adjacent: Item[] = [item];
    let cursor = index + 1;
    while (cursor < items.length && canJoinExplorationGroup(items[cursor])) {
      adjacent.push(items[cursor]);
      cursor += 1;
    }

    if (adjacent.length === 1) {
      entries.push({ type: "item", id: item.id, item });
    } else {
      entries.push({
        type: "activity_group",
        id: `exploration:${item.id}`,
        activityKind: "exploration",
        items: adjacent,
      });
    }
    index = cursor;
  }

  return entries;
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
